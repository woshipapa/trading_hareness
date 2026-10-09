#!/usr/bin/env python3
"""B300 协作分支的飞书提醒：执行方推送新提交时，往运维群发一条摘要。

B300 实验由内网机器上的执行方 agent 跑，双方只通过 GitHub 分支
``crossarch/b300-collab`` 交互（对方不能 ssh）。以前要等仓主来问，才知道对方推了
什么、是不是卡在等修复上。supervisor 每 5 分钟跑一次本脚本（任务
``b300-collab.watch``）：

* 先跑进展看板（``b300_collab_dashboard.py``）：拉取分支的稀疏克隆，与上一轮快照对比，
  把变化（任务状态、执行方回复审查、新提案、新运行目录、decide 结果、H100 阶梯的档位）
  记成事件，写进 ``state/b300-collab-events.jsonl``；
* 从同一个克隆读分支最新 50 个提交（克隆不可用时退回已登录的 ``gh``），只看执行方
  （作者 ``b300-exec-agent``）比上次记录更新的提交；
* 有执行方新提交，或有值得单独发的事件，就发一条纯文本摘要：提交、状态变化，以及
  "需要审查方处理"（按状态判：新提案、执行方回复、审计失败、选出锁频、H100 阶梯停下；
  提交标题里的 FAIL、等修复等关键词作兜底）；发出后把这些事件标成已发，再重画看板。

发送复用 Paper-KB 的 ``notify.send_to_webhook``（群机器人 webhook），不另写传输层。
webhook 地址只从环境变量 ``B300_COLLAB_FEISHU_WEBHOOK_URL`` 读（supervisor 从
``config/secrets/.env.local`` 注入），不写盘、不进日志；没配置或不是飞书机器人地址时，
只把消息写进日志。

状态（上次看到的提交）在 ``state/b300-collab-watch.json``。首次运行只记基线、不发送；
发送失败不推进状态，下一轮重试。``--dry-run`` 只打印，不发送也不写状态。
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Optional

ROOT = Path(__file__).resolve().parents[1]
REPO = "woshipapa/horizonal_fuse_kernel"
BRANCH = "crossarch/b300-collab"
BRANCH_URL = f"https://github.com/{REPO}/commits/{BRANCH}"
EXECUTOR_AUTHOR = os.environ.get("B300_COLLAB_EXECUTOR_AUTHOR", "b300-exec-agent")
STATE_PATH = Path(os.environ.get("B300_COLLAB_WATCH_STATE", str(ROOT / "state" / "b300-collab-watch.json")))
PAPER_KB = Path(os.environ.get("PAPER_KB_DIR", str(Path.home() / "codebase" / "literature_maps" / "paper_kb")))
WEBHOOK_ENV = "B300_COLLAB_FEISHU_WEBHOOK_URL"
WEBHOOK_PREFIX = "https://open.feishu.cn/open-apis/bot/v2/hook/"
PER_PAGE = 50
DASHBOARD_URL = "http://127.0.0.1:8888/b300"
CLONE = Path(os.environ.get("B300_DASH_CLONE", str(ROOT / "state" / "b300-collab-repo")))
EVENT_MAX_AGE_H = 24       # 更早还没发的事件不再进消息（只留在看板上）
LIMIT = 3500          # 飞书单条文本上限（与 notify.LIMIT 相同）
SUBJECT_LIMIT = 160
# 提交标题里出现这些词时，单列为"需要审查方处理"。"check 0 FAIL"、"0 失败"这类零计数
# 是通过，不算（2026-10-09：锁频选档的 7 个正常运行因此被误列，而真正要裁定的
# "chosen=null，三档均未过"没被列出）。
ATTENTION = ("FAIL", "失败", "等修复", "需返工", "proposal:", "停", "阻塞", "求助", "问题", "不可达",
             "未过", "未通过", "不通过", "chosen=null")
ZERO_COUNT_RE = re.compile(r"(?<![\d.])0\s*(?:个\s*)?(?:FAIL|失败)")
# A selection outcome the review predicted ("chosen=null 如预期", "与第十五份审查预期一致") is not an
# attention item; failures, stops and blocks in the same subject still are.
EXPECTED_MARKERS = ("如预期", "预期一致", "符合预期")
PREDICTABLE_OUTCOMES = ("chosen=null", "未过", "未通过", "不通过")
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def log(message: str) -> None:
    print(f"{now_iso()} {message}", flush=True)


def fetch_commits() -> list[dict[str, Any]]:
    """分支最新的 PER_PAGE 个提交，新的在前：sha、作者名、标题（第一行）。"""
    out = subprocess.run(
        ["gh", "api", f"repos/{REPO}/commits?sha={BRANCH}&per_page={PER_PAGE}"],
        capture_output=True, text=True, timeout=60)
    if out.returncode != 0:
        raise RuntimeError(f"gh api exit {out.returncode}: {out.stderr.strip()[:300]}")
    commits = []
    for item in json.loads(out.stdout):
        message = str(item.get("commit", {}).get("message", ""))
        commits.append({"sha": str(item.get("sha", "")),
                        "author": str(item.get("commit", {}).get("author", {}).get("name", "")),
                        "subject": message.splitlines()[0] if message else ""})
    return commits


def fetch_commits_from_clone() -> list[dict[str, Any]]:
    """同上，但读看板刚拉取过的稀疏克隆：通知与看板看的是同一个分支 tip。"""
    if not (CLONE / ".git").exists():
        raise FileNotFoundError(f"no clone at {CLONE}")
    out = subprocess.run(["git", "-C", str(CLONE), "log", "-n", str(PER_PAGE), "--format=%H%x1f%an%x1f%s", "HEAD"],
                         capture_output=True, text=True, timeout=60)
    if out.returncode != 0:
        raise RuntimeError(f"git log exit {out.returncode}: {out.stderr.strip()[:300]}")
    commits = []
    for line in out.stdout.splitlines():
        parts = line.split("\x1f")
        if len(parts) == 3:
            commits.append({"sha": parts[0], "author": parts[1], "subject": parts[2]})
    return commits


def events_path() -> Path:
    return STATE_PATH.with_name("b300-collab-events.jsonl")


def unsent_events(now: Optional[dt.datetime] = None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """(还没发、且在 EVENT_MAX_AGE_H 小时内的事件, 过期没发的事件)。"""
    import b300_collab_dashboard as dash  # noqa: E402 - 同目录，事件文件格式只在那里定义
    now = now or dt.datetime.now(dt.timezone.utc)
    fresh, stale = [], []
    for e in dash.load_events(events_path()):
        if e.get("sent"):
            continue
        try:
            at = dt.datetime.fromisoformat(str(e.get("at", "")).replace("Z", "+00:00"))
        except ValueError:
            at = now
        (fresh if now - at <= dt.timedelta(hours=EVENT_MAX_AGE_H) else stale).append(e)
    return fresh, stale


def mark_events(ids: set[str], how: str) -> None:
    import b300_collab_dashboard as dash  # noqa: E402
    if not ids:
        return
    events = dash.load_events(events_path())
    for e in events:
        if e.get("id") in ids and not e.get("sent"):
            e["sent"] = f"{how} {now_iso()}"
    dash.write_events(events, events_path())


def new_commits(commits: list[dict[str, Any]], last_seen: str) -> tuple[list[dict[str, Any]], bool]:
    """比 last_seen 新的提交（新的在前），以及 last_seen 是否还在列表里。"""
    fresh = []
    for commit in commits:
        if commit["sha"] == last_seen:
            return fresh, True
        fresh.append(commit)
    return fresh, False


def clean(subject: str) -> str:
    subject = EMAIL_RE.sub("<email>", " ".join(subject.split()))
    return subject if len(subject) <= SUBJECT_LIMIT else subject[:SUBJECT_LIMIT - 1] + "…"


def needs_attention(subject: str) -> bool:
    text = ZERO_COUNT_RE.sub("", subject)
    words = ATTENTION
    if any(marker in text for marker in EXPECTED_MARKERS):
        words = tuple(w for w in ATTENTION if w not in PREDICTABLE_OUTCOMES)
    return any(word in text for word in words)


def build_message(executor: list[dict[str, Any]], history_gap: bool,
                  events: Optional[list[dict[str, Any]]] = None, tip: str = "") -> str:
    """一条纯文本摘要（≤ LIMIT 字）。executor：执行方的新提交，新的在前；events：看板记下的、
    还没发的变化（按时间先后）。"""
    events = events or []
    ordered = list(reversed(executor))  # 按推送先后列
    if executor:
        header = f"B300 协作：执行方推送了 {len(executor)} 个提交（最新 {executor[0]['sha'][:7]}）"
    else:
        header = f"B300 协作：状态更新（分支 {tip[:7]}）" if tip else "B300 协作：状态更新"
    lines = [f"- {c['sha'][:7]} {clean(c['subject'])}" for c in ordered]
    changes = [f"- {clean(e['text'])}" for e in events if not e.get("attention")]
    attention = ([f"- {clean(e['text'])}" for e in events if e.get("attention")]
                 + [f"- {c['sha'][:7]} {clean(c['subject'])}" for c in ordered if needs_attention(c["subject"])])
    footer = []
    if history_gap:
        footer.append(f"（上次记录的提交不在最近 {PER_PAGE} 个里：可能推送了更多提交或改写了历史，只列最近的）")
    footer += [f"看板（本机）：{DASHBOARD_URL}", BRANCH_URL]

    def render(shown: list[str], hidden: int, shown_changes: list[str], hidden_changes: int) -> str:
        parts = [header, *shown]
        if hidden:
            parts.append(f"……另有 {hidden} 个提交，见链接")
        if shown_changes or hidden_changes:
            parts += ["", "状态变化：", *shown_changes]
            if hidden_changes:
                parts.append(f"……另有 {hidden_changes} 项变化，见看板")
        if attention:
            parts += ["", "需要审查方处理：", *attention]
        parts += ["", *footer]
        return "\n".join(parts)

    shown, shown_changes = list(lines), list(changes)
    text = render(shown, 0, shown_changes, 0)
    while len(text) > LIMIT and (shown_changes or shown):
        if shown_changes:
            shown_changes.pop()
        else:
            shown.pop()
        text = render(shown, len(lines) - len(shown), shown_changes, len(changes) - len(shown_changes))
    return text[:LIMIT]


def webhook_url() -> str:
    """飞书群机器人地址；不是 https://open.feishu.cn/open-apis/bot/v2/hook/ 开头就当没配置。"""
    url = os.environ.get(WEBHOOK_ENV, "").strip()
    return url if url.startswith(WEBHOOK_PREFIX) and len(url) > len(WEBHOOK_PREFIX) else ""


def load_notify():
    sys.path.insert(0, str(PAPER_KB))
    import notify  # noqa: E402 - Paper-KB 的飞书传输层，按路径复用
    return notify


def safe_result(result: dict[str, Any], url: str) -> str:
    """发送结果里只留状态字段，并抹掉 webhook 地址（以防万一）。"""
    kept = {k: result.get(k) for k in ("status", "code", "http", "msg", "error", "ambiguous", "via")
            if result.get(k) not in (None, "")}
    text = json.dumps(kept, ensure_ascii=False)
    if url:
        text = text.replace(url, "<webhook>").replace(url[len(WEBHOOK_PREFIX):], "<token>")
    return text[:400]


def read_state() -> dict[str, Any]:
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def write_state(state: dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    os.replace(tmp, STATE_PATH)


def clone_or_gh() -> list[dict[str, Any]]:
    """看板刚拉取过的克隆优先；克隆缺失或读失败时退回 gh。"""
    try:
        return fetch_commits_from_clone()
    except Exception as exc:  # noqa: BLE001 - 退回 gh，不影响本轮
        log(f"clone unavailable ({type(exc).__name__}); reading commits with gh")
        return fetch_commits()


def run(dry_run: bool = False,
        fetch: Optional[Callable[[], list[dict[str, Any]]]] = None,
        sender: Optional[Callable[[str, str, str], dict[str, Any]]] = None,
        events_source: Optional[Callable[[], tuple[list[dict[str, Any]], list[dict[str, Any]]]]] = None) -> int:
    fetch = fetch or fetch_commits
    try:
        commits = fetch()
    except Exception as exc:  # noqa: BLE001 - 网络或 gh 登录问题，下一轮再试
        log(f"fetch failed: {type(exc).__name__}: {str(exc)[:300]}")
        return 1
    if not commits:
        log("branch returned no commits")
        return 1
    head = commits[0]["sha"]
    state = read_state()
    last_seen = str(state.get("last_seen_sha", ""))
    try:
        events, stale = (events_source or unsent_events)()
    except Exception as exc:  # noqa: BLE001 - 事件读不了就只报提交
        log(f"events unreadable: {type(exc).__name__}")
        events, stale = [], []
    if not last_seen:
        if not dry_run:
            write_state({"last_seen_sha": head, "updated_at": now_iso(), "last_event": "baseline"})
            mark_events({e["id"] for e in events + stale}, "baseline")
        log(f"baseline {head[:7]} (nothing sent)")
        return 0
    if stale and not dry_run:
        mark_events({e["id"] for e in stale}, "expired")
    fresh, found = new_commits(commits, last_seen)
    executor = [c for c in fresh if c["author"] == EXECUTOR_AUTHOR]
    triggers = [e for e in events if e.get("trigger")]
    if not executor and not triggers:
        if fresh and not dry_run:
            write_state({"last_seen_sha": head, "updated_at": now_iso(), "last_event": "others-only"})
        return 0
    text = build_message(executor, history_gap=not found, events=events, tip=head)
    key = f"b300-collab-{executor[0]['sha']}" if executor else f"b300-collab-events-{triggers[-1]['id']}"
    ids = {e["id"] for e in events}
    if dry_run:
        log(f"dry-run {len(executor)} executor commit(s), {len(events)} event(s), key {key[:24]}…\n{text}")
        return 0
    url = webhook_url()
    if not url:
        log(f"no valid {WEBHOOK_ENV}; message logged only\n{text}")
        write_state({"last_seen_sha": head, "updated_at": now_iso(), "last_event": "logged-only"})
        mark_events(ids, "logged")
        return 0
    try:
        send = sender or load_notify().send_to_webhook
        result = send(text, url, key)
    except Exception as exc:  # noqa: BLE001 - 不推进状态，下一轮重试
        log(f"send error {type(exc).__name__}; state kept at {last_seen[:7]}")
        return 1
    if result.get("status") != "sent":
        log(f"send failed {safe_result(result, url)}; state kept at {last_seen[:7]}")
        return 1
    write_state({"last_seen_sha": head, "updated_at": now_iso(), "last_event": "sent"})
    mark_events(ids, "sent")
    log(f"sent {len(executor)} executor commit(s) and {len(events)} event(s) up to {head[:7]}")
    return 0


def refresh_dashboard(render_only: bool = False) -> bool:
    """进展看板（scripts/b300_collab_dashboard.py，:8888/b300）：本轮先跑一次（拉取克隆、
    对比快照、记事件），发完消息后再 --render-only 重画（标出已发）。失败只记日志，返回 False；
    B300_COLLAB_DASHBOARD=0 关闭（这时提交从 gh 读）。"""
    script = Path(__file__).with_name("b300_collab_dashboard.py")
    if os.environ.get("B300_COLLAB_DASHBOARD", "1") == "0" or not script.exists():
        return False
    try:
        r = subprocess.run([sys.executable, str(script), *(["--render-only"] if render_only else [])],
                           capture_output=True, text=True, timeout=240)
    except (subprocess.TimeoutExpired, OSError) as exc:
        log(f"dashboard error {type(exc).__name__}")
        return False
    if r.returncode:
        log(f"dashboard rc {r.returncode}: {(r.stderr or r.stdout).strip()[-300:]}")
        return False
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true", help="只打印消息，不发送、不写状态")
    args = parser.parse_args()
    fresh_clone = False if args.dry_run else refresh_dashboard()
    # 看板本轮拉取成功时，提交从同一个克隆读（与看板同一个 tip）；否则用 gh
    rc = run(dry_run=args.dry_run, fetch=clone_or_gh if fresh_clone else fetch_commits)
    if not args.dry_run and fresh_clone:
        refresh_dashboard(render_only=True)
    return rc


if __name__ == "__main__":
    sys.exit(main())
