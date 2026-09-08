#!/usr/bin/env python3
"""
统一后台服务 supervisor：用一个 launchd 项(com.papa.svc-supervisor)管理后台服务，
全部跑在统一 venv ~/.venvs/svc/bin/python。替代 launchd 的 KeepAlive/StartInterval/StartCalendarInterval。
"""
import os, sys, time, signal, subprocess, threading, datetime, json

HOME = os.path.expanduser("~")
PY = os.path.join(HOME, ".venvs/svc/bin/python")
PKB = os.path.join(HOME, "codebase/literature_maps/paper_kb")
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
        for line in open(os.path.join(N8N, ".env"), encoding="utf-8"):
            line = line.strip()
            if line.startswith(name + "="):
                val = line.split("=", 1)[1].strip()
                if len(val) >= 2 and val[0] == val[-1] and val[0] in "'\"":
                    val = val[1:-1]
                return val
    except OSError:
        pass
    return ""


RELAY_TOKEN = _load_env_secret("RELAY_TOKEN")

TASKS = [
    # ---- 常驻 daemon (原 KeepAlive) ----
    dict(name="paperkb.server", kind="daemon",
         args=[PY, os.path.join(PKB, "kb_server.py"), "--port", "8787"],
         cwd=PKB, out=os.path.join(PKLOG, "server.log"), err=os.path.join(PKLOG, "server.log"), env={}),
    dict(name="feishu-tunnel", kind="daemon",
         args=["ssh","-i",os.path.join(HOME,".ssh/feishu_relay_edge_ed25519"),
               "-o","BatchMode=yes","-o","IdentitiesOnly=yes","-o","ServerAliveInterval=15",
               "-o","ServerAliveCountMax=3","-o","ExitOnForwardFailure=yes","-o","StrictHostKeyChecking=accept-new",
               "-N","-L","127.0.0.1:18300:127.0.0.1:18300","root@47.114.113.152"],
         cwd=HOME, out=os.path.join(N8N,"logs/feishu-tunnel.log"),
         err=os.path.join(N8N,"logs/feishu-tunnel.log"), env={}),
    # 专表监听（SQLite/WAL 事件）当前停用：本地不再跑这条低延迟链路，爱投顾
    # 三个来源全部由 edge 的 API 轮询覆盖（含 11:30-13:00 午休窗口）。默认不启动，
    # 需要恢复本地监听时设 ITOUGU_TABLE_WATCH=1 再重启 supervisor。
    *([
        dict(name="itougu-table-watch", kind="daemon",
             args=[PY, os.path.join(N8N, "scripts/itougu_table_watch.py"), "--interval", "1.5"],
             cwd=N8N, out=os.path.join(N8N, "logs/itougu-table-watch.log"),
             err=os.path.join(N8N, "logs/itougu-table-watch.log"), env={"PYTHONUNBUFFERED": "1"}),
    ] if os.environ.get("ITOUGU_TABLE_WATCH") == "1" else []),
    # ---- 定时 interval (原 StartInterval, RunAtLoad) ----
    dict(name="paperkb.arxiv", kind="interval", interval=1800, run_at_load=True,
         args=[PY, os.path.join(PKB, "jobs.py"), "arxiv"],
         cwd=PKB, out=os.path.join(PKLOG, "arxiv.log"), err=os.path.join(PKLOG, "arxiv.log"),
         env={"PATH": PATH_ENV}),
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
         env={"PATH": PATH_ENV}),
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
         env={"PATH": PATH_ENV}, catch_up_hours=36),
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
        backoff = min(backoff * 2, 60)  # 指数退避封顶60s
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
            return json.load(handle).get('scheduled_for', '')
    except (OSError, ValueError, TypeError):
        return ''

def _calendar_mark_run(t, scheduled_for):
    path = _calendar_state_path(t)
    tmp = path + '.tmp'
    try:
        os.makedirs(PKLOG, exist_ok=True)
        with open(tmp, 'w', encoding='utf-8') as handle:
            json.dump({'scheduled_for': scheduled_for,
                       'updated_at': datetime.datetime.now().isoformat()}, handle)
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
        if run_once(t) == 0:
            _calendar_mark_run(t, scheduled_for)
    while not _shutdown.is_set():
        now = datetime.datetime.now()
        nxt = _next_calendar(now, t["weekday"], t["hour"], t["minute"])
        slog(f"{t['name']} next run at {nxt:%Y-%m-%d %H:%M}")
        while datetime.datetime.now() < nxt:
            if _shutdown.is_set(): return
            time.sleep(20)
        if not _shutdown.is_set():
            slog(f"run calendar {t['name']}")
            if run_once(t) == 0:
                _calendar_mark_run(t, nxt.date().isoformat())
            time.sleep(61)  # 防同一分钟重复触发

def _daily_state_path(t):
    safe = ''.join(ch if ch.isalnum() or ch in '._-' else '_' for ch in t['name'])
    return os.path.join(PKLOG, f'.{safe}.last-run.json')

def _daily_last_run(t):
    try:
        with open(_daily_state_path(t), encoding='utf-8') as handle:
            return json.load(handle).get('date', '')
    except (OSError, ValueError, TypeError):
        return ''

def _daily_mark_run(t, date):
    path = _daily_state_path(t)
    tmp = path + '.tmp'
    try:
        os.makedirs(PKLOG, exist_ok=True)
        with open(tmp, 'w', encoding='utf-8') as handle:
            json.dump({'date': date, 'updated_at': datetime.datetime.now().isoformat()}, handle)
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
        if run_once(t) == 0:
            _daily_mark_run(t, today)
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
            if run_once(t) == 0:
                _daily_mark_run(t, datetime.datetime.now().date().isoformat())
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
