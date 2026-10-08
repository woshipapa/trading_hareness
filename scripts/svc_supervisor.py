#!/usr/bin/env python3
"""
统一后台服务 supervisor：用一个 launchd 项(com.papa.svc-supervisor)管理后台服务，
全部跑在统一 venv ~/.venvs/svc/bin/python。替代 launchd 的 KeepAlive/StartInterval/StartCalendarInterval。
"""
import os, sys, time, signal, subprocess, threading, datetime, json

HOME = os.path.expanduser("~")
PY = os.path.join(HOME, ".venvs/svc/bin/python")
PKB = os.path.join(HOME, "codebase/literature_maps/paper_kb")
HARNESS = os.environ.get("VIDEO_HARNESS_DIR", "/Users/papa/codebase/video_understanding_harness")
N8N = os.path.join(HOME, "codebase/n8n")
PKLOG = os.path.join(HOME, "Library/Logs/paper-kb")
SUP_LOG = os.path.join(N8N, "logs/svc-supervisor.log")

PATH_ENV = "/Users/papa/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
def _load_env_secret(name):
    """从进程环境或 n8n/.env(已 gitignore) 读密钥，避免硬编码入库。"""
    v = os.environ.get(name)
    if v:
        return v
    try:
        with open(os.path.join(N8N, ".env"), encoding="utf-8") as env_file:
            for line in env_file:
                line = line.strip()
                if line.startswith(name + "="):
                    val = line.split("=", 1)[1].strip()
                    if len(val) >= 2 and val[0] == val[-1] and val[0] in "'\"":
                        val = val[1:-1]
                    return val
    except OSError:
        pass
    return ""


def _paper_provider_env():
    """Pass optional paper-provider credentials only to Paper-KB children.

    The supervisor itself never logs these values.  Empty values are omitted so
    provider code can distinguish an unavailable credential from an explicitly
    configured one.
    """
    env = {"PATH": PATH_ENV}
    for name in (
        "OPENALEX_API_KEY", "OPENCITATIONS_ACCESS_TOKEN",
        "PAPER_KB_OPENALEX_ENABLED", "PAPER_KB_OPENCITATIONS_ENABLED",
        "CORE_API_KEY", "HF_TOKEN", "HUGGINGFACE_TOKEN", "UNPAYWALL_EMAIL",
        "S2_API_KEY", "S2_API_KEYS", "PAPER_KB_SCHOLAR_ALERT_DIR",
    ):
        value = _load_env_secret(name)
        if value:
            env[name] = value
    return env


RELAY_TOKEN = _load_env_secret("RELAY_TOKEN")
SCHOLAR_ALERT_DIR = _load_env_secret("PAPER_KB_SCHOLAR_ALERT_DIR")

TASKS = [
    # ---- 常驻 daemon (原 KeepAlive) ----
    # Video Understanding Harness: the NiceGUI page and FastAPI backend run in
    # one process so the supervisor owns the complete local UI/API lifecycle.
    dict(name="video-harness", kind="daemon",
         args=[PY, os.path.join(HOME, "codebase/video_understanding_harness/nicegui_app.py")],
         cwd=os.path.join(HOME, "codebase/video_understanding_harness"),
         out=os.path.join(HOME, "Library/Logs/video-understanding-harness.log"),
         err=os.path.join(HOME, "Library/Logs/video-understanding-harness.log"),
         env={"PATH": PATH_ENV, "PYTHONUNBUFFERED": "1", "VIDEO_HARNESS_PORT": "8765"}),
    # Shared model registry/API.  Keep it on the existing documented local
    # port; the document worker itself uses durable job directories and does
    # not open another listener.
    dict(name="model-service", kind="daemon",
         args=[PY, "-m", "model_service.app"],
         cwd=os.path.join(HOME, "codebase"),
         out=os.path.join(HOME, "Library/Logs/model-service.log"),
         err=os.path.join(HOME, "Library/Logs/model-service.log"),
         env={"PATH": PATH_ENV, "PYTHONUNBUFFERED": "1", "MODEL_SERVICE_HOST": "127.0.0.1",
              "MODEL_SERVICE_PORT": "8791", "MODEL_SERVICE_DOCUMENT_INPUT_ROOT": "/Users/papa/Downloads",
              "MODEL_SERVICE_DOCUMENT_OUTPUT_ROOT": "/Users/papa/Downloads"}),
    dict(name="paperkb.server", kind="daemon",
         args=[PY, os.path.join(PKB, "kb_server.py"), "--port", "8787"],
         cwd=PKB, out=os.path.join(PKLOG, "server.log"), err=os.path.join(PKLOG, "server.log"), env={}),
    # owner 只读路径的 ssh 隧道。整条老师复盘链都靠它；以前没人托管，它一断
    # teacher.cycle 就一直 hold，症状只是"owner 读路径不可用"，很容易以为是别的事。
    # 值从 n8n/.env（已 gitignore）在内存里读，不落盘、不进日志。
    *([dict(name="owner-tunnel", kind="daemon",
            args=["ssh", "-NT", "-o", "BatchMode=yes", "-o", "ExitOnForwardFailure=yes",
                  "-o", "ServerAliveInterval=30", "-o", "ServerAliveCountMax=3",
                  "-o", "StrictHostKeyChecking=yes", "-i", _load_env_secret("LONGHU_SSH_KEY_PATH"),
                  "-p", _load_env_secret("LONGHU_SSH_PORT") or "22",
                  "-L", f"127.0.0.1:{_load_env_secret('LONGHU_LOCAL_PORT') or '15682'}"
                        f":127.0.0.1:{_load_env_secret('LONGHU_REMOTE_PORT') or '15682'}",
                  f"{_load_env_secret('LONGHU_SSH_USER')}@{_load_env_secret('LONGHU_SSH_HOST')}"],
            cwd=HOME, out=os.path.join(N8N, "logs/owner-tunnel.log"),
            err=os.path.join(N8N, "logs/owner-tunnel.log"), env={})]
      if all(_load_env_secret(name) for name in
             ("LONGHU_SSH_KEY_PATH", "LONGHU_SSH_USER", "LONGHU_SSH_HOST")) else []),
    dict(name="feishu-tunnel", kind="daemon",
         args=["ssh","-i",os.path.join(HOME,".ssh/feishu_relay_edge_ed25519"),
               "-o","BatchMode=yes","-o","IdentitiesOnly=yes","-o","ServerAliveInterval=15",
               "-o","ServerAliveCountMax=3","-o","ExitOnForwardFailure=yes","-o","StrictHostKeyChecking=accept-new",
               "-N","-L","127.0.0.1:18300:127.0.0.1:18300","root@47.114.113.152"],
         cwd=HOME, out=os.path.join(N8N,"logs/feishu-tunnel.log"),
         err=os.path.join(N8N,"logs/feishu-tunnel.log"), env={}),
    # 小红书 edge API 的 loopback forward。采集器一直运行在 edge，本地只
    # 领取摘要任务并调用 Paper-KB 的 codex-teleai provider。
    dict(name="xhs-edge-tunnel", kind="daemon",
         args=["ssh","-i",os.path.join(HOME,".ssh/feishu_relay_edge_ed25519"),
               "-o","BatchMode=yes","-o","IdentitiesOnly=yes","-o","ServerAliveInterval=15",
               "-o","ServerAliveCountMax=3","-o","ExitOnForwardFailure=yes","-o","StrictHostKeyChecking=accept-new",
               "-N","-L","127.0.0.1:18790:127.0.0.1:18790","root@47.114.113.152"],
         cwd=HOME, out=os.path.join(N8N,"logs/xhs-edge-tunnel.log"),
         err=os.path.join(N8N,"logs/xhs-edge-tunnel.log"), env={}),
    dict(name="xhs-ai-worker", kind="daemon",
         args=[PY, os.path.join(N8N, "xhs-intel/local_worker.py")],
         cwd=N8N, out=os.path.join(N8N, "logs/xhs-ai-worker.log"),
         err=os.path.join(N8N, "logs/xhs-ai-worker.log"),
         env={"PATH": PATH_ENV, "PYTHONUNBUFFERED": "1",
              "XHS_EDGE_URL": "http://127.0.0.1:18790",
              "XHS_COLLECTOR_TOKEN": _load_env_secret("XHS_COLLECTOR_TOKEN"),
              "XHS_AI_WORKER_ID": "mac-codex-teleai"}),
    # Paper-KB lives on this workstation while the always-on Feishu adapter
    # runs on edge.  Keep its command webhooks on a loopback-only reverse SSH
    # forward so 收录/查询/反馈 do not depend on an edge n8n workflow copy.
    dict(name="paper-kb-webhook-tunnel", kind="daemon",
         args=["ssh","-i",os.path.join(HOME,".ssh/feishu_relay_edge_ed25519"),
               "-o","BatchMode=yes","-o","IdentitiesOnly=yes","-o","ServerAliveInterval=15",
               "-o","ServerAliveCountMax=3","-o","ExitOnForwardFailure=yes","-o","StrictHostKeyChecking=accept-new",
               "-N","-R","127.0.0.1:15678:127.0.0.1:5678","root@47.114.113.152"],
         cwd=HOME, out=os.path.join(N8N,"logs/paper-kb-webhook-tunnel.log"),
         err=os.path.join(N8N,"logs/paper-kb-webhook-tunnel.log"), env={},
         # A remote listener collision can persist across several SSH retries;
         # avoid reconnecting every minute while keeping the tunnel self-healing.
         restart_backoff_max=300),
    # 专表监听（SQLite/WAL 事件）当前停用：本地不再跑这条低延迟链路，爱投顾
    # 三个来源全部由 edge 的 API 轮询覆盖（含 11:30-13:00 午休窗口）。默认不启动，
    # 需要恢复本地监听时设 ITOUGU_TABLE_WATCH=1 再重启 supervisor。
    *([
        dict(name="itougu-table-watch", kind="daemon",
             args=[PY, os.path.join(N8N, "feishu-relay/scripts/sources/itougu/itougu_table_watch.py"), "--interval", "1.5"],
             cwd=N8N, out=os.path.join(N8N, "logs/itougu-table-watch.log"),
             err=os.path.join(N8N, "logs/itougu-table-watch.log"), env={"PYTHONUNBUFFERED": "1"}),
    ] if os.environ.get("ITOUGU_TABLE_WATCH") == "1" else []),
    # ---- 定时 interval (原 StartInterval, RunAtLoad) ----
    # 老师复盘的每日闭环：结算已由 peer 的 peer_close_research 自己跑，策略稿已由
    # harness 自动写，缺的是"策略稿 → 策略包 → 入池"和方法蒸馏这两段。每 20 分钟
    # 看一眼最新任务，条件不足就什么都不做（gate 会拦住证据缺失的稿子）。
    # 自动入池自 2026-09-25 起默认开启（owner 要求）。仍然过三道闸：gate（owner 通、
    # 交接清单 completed、无阻断代码问题、正文核对 >= 50 个数字）、check 的 problems
    # 为空、以及导入时间窗（目标就是今天且已开盘/已收盘一律不自动入池）。
    # 设 TEACHER_CYCLE_AUTO_IMPORT=0 可关掉。
    # 视频投递：47 edge 上的 itougu relay 每晚把「猎场擒龙内参」的复盘视频写成一行
    # JSON 追加到 video-tasks.jsonl；这里读那份队列，有新的就建 harness 任务。
    # 老师 19:10~21:40 之间发，所以 18:00 后每 10 分钟看一眼；一轮最多投一个。
    dict(name="video.ingest", kind="interval", interval=600, run_at_load=False,
         args=[PY, os.path.join(HARNESS, "ingest.py"), "--scheduled"],
         cwd=HARNESS, out=os.path.join(N8N, "logs/video-ingest.log"),
         err=os.path.join(N8N, "logs/video-ingest.log"),
         env={"PATH": PATH_ENV, "PYTHONUNBUFFERED": "1",
              # NiceGUI 把 FastAPI 后端挂在 /backend 下；少了这一段建任务是 404。
              "VIDEO_HARNESS_BASE_URL": os.environ.get("VIDEO_HARNESS_BASE_URL",
                                                       "http://127.0.0.1:8765/backend")}),
    dict(name="teacher.cycle", kind="interval", interval=1200, run_at_load=False,
         args=[PY, os.path.join(N8N, "scripts/teacher_cycle.py")],
         cwd=N8N, out=os.path.join(N8N, "logs/teacher-cycle.log"),
         err=os.path.join(N8N, "logs/teacher-cycle.log"),
         env={"PATH": PATH_ENV, "PYTHONUNBUFFERED": "1",
              "VIDEO_RESEARCH_API_BASE_URL": "http://127.0.0.1:15682",
              "TEACHER_CYCLE_AUTO_IMPORT": os.environ.get("TEACHER_CYCLE_AUTO_IMPORT", "1")}),
    dict(name="paperkb.arxiv", kind="interval", interval=1800, run_at_load=True,
         args=[PY, os.path.join(PKB, "jobs.py"), "arxiv"],
         cwd=PKB, out=os.path.join(PKLOG, "arxiv.log"), err=os.path.join(PKLOG, "arxiv.log"),
         env={"PATH": PATH_ENV}),
    # Optional discovery supplements write replayable snapshots only; they do
    # not mutate the corpus or replace the arXiv daily lane.  DataCite is
    # public and bounded.  Hugging Face is enabled only when a runtime token is
    # actually present, because its API may require authentication.  The HF
    # lane intentionally asks for ``trending`` so supplemental_digest can use
    # the provider's recommendation signal in its deterministic ranking.
    dict(name="paperkb.sources", kind="daily", hour=11, minute=0, run_at_load=False,
         args=[PY, os.path.join(PKB, "source_refresh.py"), "--source", "datacite",
               "--query", "large language model mixture of experts distributed systems",
               "--limit", "50"],
         cwd=PKB, out=os.path.join(PKLOG, "sources.log"), err=os.path.join(PKLOG, "sources.log"),
         env={"PATH": PATH_ENV}),
    dict(name="paperkb.repository-sources", kind="daily", hour=11, minute=10, run_at_load=False,
         args=[PY, os.path.join(PKB, "source_refresh.py"), "--source", "europe_pmc",
               "--source", "zenodo", "--query",
               "large language model mixture of experts distributed systems", "--limit", "25"],
         cwd=PKB, out=os.path.join(PKLOG, "repository-sources.log"),
         err=os.path.join(PKLOG, "repository-sources.log"), env={"PATH": PATH_ENV}),
    *([dict(name="paperkb.repository-sources-core", kind="daily", hour=11, minute=20, run_at_load=False,
            args=[PY, os.path.join(PKB, "source_refresh.py"), "--source", "core", "--limit", "25"],
            cwd=PKB, out=os.path.join(PKLOG, "repository-sources-core.log"),
            err=os.path.join(PKLOG, "repository-sources-core.log"),
            env={"PATH": PATH_ENV, "CORE_API_KEY": _load_env_secret("CORE_API_KEY")})]
      if _load_env_secret("CORE_API_KEY") else []),
    *([dict(name="paperkb.sources-hf", kind="daily", hour=11, minute=10, run_at_load=False,
            args=[PY, os.path.join(PKB, "source_refresh.py"), "--source", "huggingface_daily",
                  "--sort", "trending", "--limit", "50"],
            cwd=PKB, out=os.path.join(PKLOG, "sources-hf.log"), err=os.path.join(PKLOG, "sources-hf.log"),
            env={"PATH": PATH_ENV, "HF_TOKEN": (_load_env_secret("HF_TOKEN") or _load_env_secret("HUGGINGFACE_TOKEN"))})]
      if (_load_env_secret("HF_TOKEN") or _load_env_secret("HUGGINGFACE_TOKEN")) else []),
    *([dict(name="paperkb.scholar-alerts", kind="daily", hour=11, minute=15, run_at_load=False,
            args=[PY, os.path.join(PKB, "source_refresh.py"), "--source", "scholar_alerts",
                  "--query", SCHOLAR_ALERT_DIR, "--limit", "100"],
            cwd=PKB, out=os.path.join(PKLOG, "scholar-alerts.log"),
            err=os.path.join(PKLOG, "scholar-alerts.log"), env={"PATH": PATH_ENV})]
      if SCHOLAR_ALERT_DIR else []),
    dict(name="paperkb.supplemental-digest", kind="daily", hour=11, minute=30, run_at_load=False,
         args=[PY, os.path.join(PKB, "supplemental_digest.py"), "--limit", "20", "--notify"],
         cwd=PKB, out=os.path.join(PKLOG, "supplemental-digest.log"),
         err=os.path.join(PKLOG, "supplemental-digest.log"), env={"PATH": PATH_ENV}),
    # One bounded canary audit after the snapshot pulls.  It covers every
    # registered provider/interface, reports not_run/not_configured explicitly,
    # and sends the persisted capability matrix through the durable job path.
    dict(name="paperkb.source-health", kind="daily", hour=11, minute=45, run_at_load=True,
         args=[PY, os.path.join(PKB, "jobs.py"), "source-health"],
         cwd=PKB, out=os.path.join(PKLOG, "source-health.log"),
         err=os.path.join(PKLOG, "source-health.log"),
         env={**_paper_provider_env(), "PAPER_KB_SOURCE_HEALTH_TASK": "1"}),
    # Ingest is successful once a paper is searchable, even when the LLM
    # provider is temporarily exhausted.  Retry only persisted missing specs;
    # completed specs are removed from the queue before any provider call.
    dict(name="paperkb.distill-retry", kind="daily", hour=12, minute=0, run_at_load=False,
         args=[PY, os.path.join(PKB, "jobs.py"), "distill-retry"],
         cwd=PKB, out=os.path.join(PKLOG, "distill-retry.log"),
         err=os.path.join(PKLOG, "distill-retry.log"), env={"PATH": PATH_ENV}),
    dict(name="paperkb.refresh", kind="interval", interval=120, run_at_load=True,
         args=[PY, os.path.join(PKB, "kb_refresh.py")],
         cwd=PKB, out=os.path.join(PKLOG, "refresh.log"), err=os.path.join(PKLOG, "refresh.log"), env={}),
    # 每日学习提醒统一由 supervisor 的 svc venv 启动；不要再为 paper-kb
    # 单独创建 launchd 项，避免解释器/环境和重启策略分叉。
    dict(name="paperkb.reminders", kind="daily", hour=8, minute=30, run_at_load=True,
         args=[PY, os.path.join(PKB, "learning_dispatch.py")],
         cwd=PKB, out=os.path.join(PKLOG, "reminders.log"), err=os.path.join(PKLOG, "reminders.log"),
         env={"PATH": PATH_ENV}),
    # 周报若被 S2 限流而 partial，午间只重试未完成周；完成周会立即 no-op。
    dict(name="paperkb.s2-recovery", kind="daily", hour=12, minute=15, run_at_load=True,
         args=[PY, os.path.join(PKB, "jobs.py"), "s2-weekly"],
         cwd=PKB, out=os.path.join(PKLOG, "s2-recovery.log"), err=os.path.join(PKLOG, "s2-recovery.log"),
         env=_paper_provider_env()),
    # workspace.db/citations.db/search.db/learning_delivery.db 记录着 FSRS 复习
    # 历史、collections、引用反馈标签——PDF 有 iCloud，这些数据库此前没有任何
    # 备份路径。sqlite3 .backup + gzip 到 iCloud，见 backup_sqlite.sh。
    dict(name="paperkb.backup", kind="daily", hour=3, minute=30, run_at_load=False,
         args=["/bin/bash", os.path.join(PKB, "backup_sqlite.sh")],
         cwd=PKB, out=os.path.join(PKLOG, "backup.log"), err=os.path.join(PKLOG, "backup.log"),
         env={"PATH": PATH_ENV}),
    # 引用触发发现：每周扫一次 workspace collections 里的种子论文，谁新引用了
    # 它们就推到 Feishu（幂等，见过的 citer 不重复报）。周三上午，避开周二的 S2 周报。
    dict(name="paperkb.citation-watch", kind="calendar", weekday=3, hour=9, minute=30,
         args=[PY, os.path.join(PKB, "citation_watch.py"), "--notify"],
         cwd=PKB, out=os.path.join(PKLOG, "citation-watch.log"), err=os.path.join(PKLOG, "citation-watch.log"),
         env=_paper_provider_env(), catch_up_hours=36),
    # ---- 观察池自动化:本地是唯一真源,edge 是扫描方 ----
    # sync 每 5 分钟推差异到 edge(diff 后才 PUT,避免无谓触发 45 天 hydration);
    # refresh 每 30 分钟触发,脚本内按沪时窗口+当日标记自行决定是否真正执行。
    dict(name="watchlist.sync", kind="interval", interval=300, run_at_load=True,
         args=["/bin/bash", os.path.join(N8N, "scripts/sync-watchlist-to-edge.sh")],
         cwd=N8N, out=os.path.join(N8N, "logs/watchlist-sync.log"),
         err=os.path.join(N8N, "logs/watchlist-sync.log"), env={"PATH": PATH_ENV}),
    dict(name="watchlist.refresh", kind="interval", interval=1800, run_at_load=False,
         args=[PY, os.path.join(N8N, "scripts/refresh-watchlist-from-proposals.py")],
         cwd=N8N, out=os.path.join(N8N, "logs/watchlist-refresh.log"),
         err=os.path.join(N8N, "logs/watchlist-refresh.log"), env={"PATH": PATH_ENV}),
    # ---- 周更 calendar (原 StartCalendarInterval: 周一 09:00) ----
    dict(name="paperkb.harvest", kind="calendar", weekday=1, hour=9, minute=0,
         args=[PY, os.path.join(PKB, "kb_harvest.py")],
         cwd=PKB, out=os.path.join(PKLOG, "harvest.log"), err=os.path.join(PKLOG, "harvest.log"),
         env={"PATH": PATH_ENV}, catch_up_hours=36),
    dict(name="paperkb.s2", kind="calendar", weekday=2, hour=10, minute=15,
         args=[PY, os.path.join(PKB, "jobs.py"), "s2-weekly"],
         cwd=PKB, out=os.path.join(PKLOG, "s2.log"), err=os.path.join(PKLOG, "s2.log"),
         env=_paper_provider_env(), catch_up_hours=36),
    # Recommendation evaluation belongs beside the Paper-KB producer clocks.
    # The n8n trigger remains a compatibility path, while jobs.py suppresses a
    # duplicate run within the following day.
    dict(name="paperkb.eval-recommendations", kind="calendar", weekday=2, hour=20, minute=30,
         args=[PY, os.path.join(PKB, "jobs.py"), "eval-recommendations"],
         cwd=PKB, out=os.path.join(PKLOG, "eval-recommendations.log"),
         err=os.path.join(PKLOG, "eval-recommendations.log"),
         env={"PATH": PATH_ENV}, catch_up_hours=36),
]

_shutdown = threading.Event()
_procs = {}  # name -> Popen (daemons)

def slog(msg):
    line = f"{datetime.datetime.now():%Y-%m-%d %H:%M:%S} [supervisor] {msg}\n"
    try:
        with open(SUP_LOG, "a") as f: f.write(line)
    except OSError: pass
    sys.stderr.write(line)

def _open_out(t):
    outf = open(t["out"], "a")
    errf = outf if t["err"] == t["out"] else open(t["err"], "a")
    return outf, errf

def _child_env(t):
    e = dict(os.environ)
    e.update(t.get("env", {}))
    return e

def run_once(t):
    """跑一次并等待结束(interval/calendar)。"""
    outf, errf = _open_out(t)
    try:
        p = subprocess.Popen(t["args"], cwd=t["cwd"], env=_child_env(t), stdout=outf, stderr=errf)
        while p.poll() is None:
            if _shutdown.is_set():
                p.terminate()
                try: p.wait(10)
                except subprocess.TimeoutExpired: p.kill()
                break
            time.sleep(1)
        return p.returncode
    finally:
        outf.close()
        if errf is not outf: errf.close()

def daemon_loop(t):
    backoff = 1
    backoff_max = max(1, int(t.get("restart_backoff_max", 60)))
    while not _shutdown.is_set():
        outf, errf = _open_out(t)
        try:
            slog(f"start daemon {t['name']}")
            p = subprocess.Popen(t["args"], cwd=t["cwd"], env=_child_env(t), stdout=outf, stderr=errf)
            _procs[t["name"]] = p
            while p.poll() is None:
                if _shutdown.is_set():
                    p.terminate()
                    try: p.wait(10)
                    except subprocess.TimeoutExpired: p.kill()
                    break
                time.sleep(1)
            rc = p.returncode
        finally:
            outf.close()
            if errf is not outf: errf.close()
        if _shutdown.is_set(): break
        slog(f"daemon {t['name']} exited rc={rc}; restart in {backoff}s")
        _shutdown.wait(backoff)
        backoff = min(backoff * 2, backoff_max)  # 指数退避，任务可提高封顶值
        # 正常存活重置退避
        if rc == 0: backoff = 1

def interval_loop(t):
    if t.get("run_at_load"): 
        if not _shutdown.is_set(): run_once(t)
    while not _shutdown.is_set():
        start = time.time()
        # 睡到下个周期(可被 shutdown 打断)
        while time.time() - start < t["interval"]:
            if _shutdown.is_set(): return
            time.sleep(min(5, t["interval"]))
        if not _shutdown.is_set(): run_once(t)

def _next_calendar(now, weekday, hour, minute):
    # launchd Weekday: 1=周一..7=周日,0=周日; 用 isoweekday(周一=1..周日=7)匹配(0视作7)
    target = 7 if weekday == 0 else weekday
    for d in range(0, 8):
        cand = (now + datetime.timedelta(days=d)).replace(hour=hour, minute=minute, second=0, microsecond=0)
        if cand > now and cand.isoweekday() == target:
            return cand
    return now + datetime.timedelta(days=7)

def _previous_calendar(now, weekday, hour, minute):
    """Return the most recent scheduled occurrence at or before ``now``."""
    target = 7 if weekday == 0 else weekday
    candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate > now:
        candidate -= datetime.timedelta(days=1)
    delta = (candidate.isoweekday() - target) % 7
    return candidate - datetime.timedelta(days=delta)

def _calendar_state_path(t):
    safe = ''.join(ch if ch.isalnum() or ch in '._-' else '_' for ch in t['name'])
    return os.path.join(PKLOG, f'.{safe}.last-run.json')

def _calendar_last_run(t):
    try:
        with open(_calendar_state_path(t), encoding='utf-8') as handle:
            payload = json.load(handle)
            status = str(payload.get('status', 'success')).lower()
            exit_code = payload.get('exit_code', 0)
            if status not in {'ok', 'success', 'succeeded'}:
                return ''
            try:
                if int(exit_code) != 0:
                    return ''
            except (TypeError, ValueError):
                return ''
            return payload.get('scheduled_for', '')
    except (OSError, ValueError, TypeError):
        return ''

def _calendar_mark_run(t, scheduled_for, *, status='success', exit_code=0, duration_s=None):
    path = _calendar_state_path(t)
    tmp = path + '.tmp'
    try:
        os.makedirs(PKLOG, exist_ok=True)
        receipt = {'scheduled_for': scheduled_for,
                   'updated_at': datetime.datetime.now().isoformat(),
                   'status': status, 'exit_code': exit_code}
        if duration_s is not None:
            receipt['duration_s'] = round(float(duration_s), 3)
        with open(tmp, 'w', encoding='utf-8') as handle:
            json.dump(receipt, handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except OSError:
        try: os.unlink(tmp)
        except OSError: pass

def calendar_loop(t):
    # A sleeping Mac can cross a weekly clock.  Catch up only a recent missed
    # occurrence; an old first-start must not unexpectedly replay last week's
    # expensive harvest/S2 run.  The producer itself remains idempotent.
    now = datetime.datetime.now()
    scheduled = _previous_calendar(now, t['weekday'], t['hour'], t['minute'])
    catch_up_hours = max(0, float(t.get('catch_up_hours', 0)))
    if (catch_up_hours and now >= scheduled
            and (now - scheduled).total_seconds() <= catch_up_hours * 3600
            and _calendar_last_run(t) != scheduled.date().isoformat()
            and not _shutdown.is_set()):
        scheduled_for = scheduled.date().isoformat()
        slog(f"run calendar catch-up {t['name']} scheduled_for={scheduled_for}")
        started = time.monotonic()
        rc = run_once(t)
        duration = time.monotonic() - started
        _calendar_mark_run(
            t, scheduled_for, status='success' if rc == 0 else 'failed',
            exit_code=rc, duration_s=duration,
        )
        slog(f"calendar catch-up {t['name']} rc={rc} duration={duration:.1f}s")
    while not _shutdown.is_set():
        now = datetime.datetime.now()
        nxt = _next_calendar(now, t["weekday"], t["hour"], t["minute"])
        slog(f"{t['name']} next run at {nxt:%Y-%m-%d %H:%M}")
        while datetime.datetime.now() < nxt:
            if _shutdown.is_set(): return
            time.sleep(20)
        if not _shutdown.is_set():
            slog(f"run calendar {t['name']}")
            started = time.monotonic()
            rc = run_once(t)
            duration = time.monotonic() - started
            _calendar_mark_run(
                t, nxt.date().isoformat(), status='success' if rc == 0 else 'failed',
                exit_code=rc, duration_s=duration,
            )
            slog(f"calendar {t['name']} rc={rc} duration={duration:.1f}s")
            time.sleep(61)  # 防同一分钟重复触发

def _daily_state_path(t):
    safe = ''.join(ch if ch.isalnum() or ch in '._-' else '_' for ch in t['name'])
    return os.path.join(PKLOG, f'.{safe}.last-run.json')

def _daily_last_run(t):
    try:
        with open(_daily_state_path(t), encoding='utf-8') as handle:
            payload = json.load(handle)
            # Older markers only had date/updated_at and are considered a
            # successful receipt for backwards compatibility.  New failed
            # receipts deliberately do not suppress catch-up after a restart.
            status = str(payload.get('status', 'success')).lower()
            exit_code = payload.get('exit_code', 0)
            if status not in {'ok', 'success', 'succeeded'}:
                return ''
            try:
                if int(exit_code) != 0:
                    return ''
            except (TypeError, ValueError):
                return ''
            return payload.get('date', '')
    except (OSError, ValueError, TypeError):
        return ''

def _daily_mark_run(t, date, *, status='success', exit_code=0, duration_s=None):
    path = _daily_state_path(t)
    tmp = path + '.tmp'
    try:
        os.makedirs(PKLOG, exist_ok=True)
        receipt = {
            'date': date,
            'updated_at': datetime.datetime.now().isoformat(),
            'status': status,
            'exit_code': exit_code,
        }
        if duration_s is not None:
            receipt['duration_s'] = round(float(duration_s), 3)
        with open(tmp, 'w', encoding='utf-8') as handle:
            json.dump(receipt, handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except OSError:
        try: os.unlink(tmp)
        except OSError: pass

def daily_loop(t):
    """Run once per local clock day at the configured hour/minute."""
    # A restart/wake after the scheduled minute must not silently lose the
    # reminder. Persist one local date per task; producers are idempotent, so a
    # crash before the mark simply causes one safe retry.
    now = datetime.datetime.now()
    today = now.date().isoformat()
    scheduled = now.replace(hour=t['hour'], minute=t['minute'], second=0, microsecond=0)
    if t.get("run_at_load") and now >= scheduled and _daily_last_run(t) != today and not _shutdown.is_set():
        slog(f"run daily catch-up {t['name']}")
        started = time.monotonic()
        rc = run_once(t)
        duration = time.monotonic() - started
        status = 'success' if rc == 0 else 'failed'
        _daily_mark_run(t, today, status=status, exit_code=rc, duration_s=duration)
        slog(f"daily catch-up {t['name']} rc={rc} duration={duration:.1f}s")
    while not _shutdown.is_set():
        now = datetime.datetime.now()
        nxt = now.replace(hour=t["hour"], minute=t["minute"], second=0, microsecond=0)
        if nxt <= now:
            nxt += datetime.timedelta(days=1)
        slog(f"{t['name']} next run at {nxt:%Y-%m-%d %H:%M}")
        while datetime.datetime.now() < nxt:
            if _shutdown.is_set(): return
            time.sleep(20)
        if not _shutdown.is_set():
            slog(f"run daily {t['name']}")
            started = time.monotonic()
            rc = run_once(t)
            duration = time.monotonic() - started
            _daily_mark_run(
                t,
                datetime.datetime.now().date().isoformat(),
                status='success' if rc == 0 else 'failed',
                exit_code=rc,
                duration_s=duration,
            )
            slog(f"daily {t['name']} rc={rc} duration={duration:.1f}s")
            time.sleep(61)

def _handle_term(signum, frame):
    slog(f"got signal {signum}, shutting down")
    _shutdown.set()

def main():
    os.makedirs(PKLOG, exist_ok=True)
    os.makedirs(os.path.dirname(SUP_LOG), exist_ok=True)
    signal.signal(signal.SIGTERM, _handle_term)
    signal.signal(signal.SIGINT, _handle_term)
    slog(f"supervisor up, python={PY}, {len(TASKS)} tasks")
    threads = []
    for t in TASKS:
        fn = {"daemon": daemon_loop, "interval": interval_loop, "calendar": calendar_loop,
              "daily": daily_loop}[t["kind"]]
        th = threading.Thread(target=fn, args=(t,), name=t["name"], daemon=True)
        th.start(); threads.append(th)
    while not _shutdown.is_set():
        time.sleep(1)
    slog("waiting tasks to stop")
    for th in threads: th.join(timeout=15)
    slog("supervisor exit")

if __name__ == "__main__":
    main()
