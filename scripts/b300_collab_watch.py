#!/usr/bin/env python3
"""B300 协作分支的飞书提醒：执行方推送新提交时，往运维群发一条摘要。

B300 实验由内网机器上的执行方 agent 跑，双方只通过 GitHub 分支
``crossarch/b300-collab`` 交互（对方不能 ssh）。以前要等仓主来问，才知道对方推了
什么、是不是卡在等修复上。supervisor 每 5 分钟跑一次本脚本（任务
``b300-collab.watch``）：

* 用已登录的 ``gh``（keyring）读分支最新 50 个提交；
* 只看执行方（作者 ``b300-exec-agent``）比上次记录更新的提交；
* 有新提交就发一条纯文本摘要，FAIL、等修复、提案这类提交另列"需要审查方处理"。

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
LIMIT = 3500          # 飞书单条文本上限（与 notify.LIMIT 相同）
SUBJECT_LIMIT = 160
# 提交标题里出现这些词时，单列为"需要审查方处理"。
ATTENTION = ("FAIL", "等修复", "需返工", "proposal:", "停", "阻塞", "求助", "问题", "不可达")
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
    return any(word in subject for word in ATTENTION)


def build_message(executor: list[dict[str, Any]], history_gap: bool) -> str:
    """一条纯文本摘要（≤ LIMIT 字）。executor：执行方的新提交，新的在前。"""
    ordered = list(reversed(executor))  # 按推送先后列
    header = f"B300 协作：执行方推送了 {len(executor)} 个提交（最新 {executor[0]['sha'][:7]}）"
    lines = [f"- {c['sha'][:7]} {clean(c['subject'])}" for c in ordered]
    attention = [f"- {c['sha'][:7]} {clean(c['subject'])}" for c in ordered if needs_attention(c["subject"])]
    footer = []
    if history_gap:
        footer.append(f"（上次记录的提交不在最近 {PER_PAGE} 个里：可能推送了更多提交或改写了历史，只列最近的）")
    footer.append(BRANCH_URL)

    def render(shown: list[str], hidden: int) -> str:
        parts = [header, *shown]
        if hidden:
            parts.append(f"……另有 {hidden} 个提交，见链接")
        if attention:
            parts += ["", "需要审查方处理：", *attention]
        parts += ["", *footer]
        return "\n".join(parts)

    shown = list(lines)
    text = render(shown, 0)
    while len(text) > LIMIT and shown:
        shown.pop()
        text = render(shown, len(lines) - len(shown))
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


def run(dry_run: bool = False,
        fetch: Callable[[], list[dict[str, Any]]] = fetch_commits,
        sender: Optional[Callable[[str, str, str], dict[str, Any]]] = None) -> int:
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
    if not last_seen:
        if not dry_run:
            write_state({"last_seen_sha": head, "updated_at": now_iso(), "last_event": "baseline"})
        log(f"baseline {head[:7]} (nothing sent)")
        return 0
    fresh, found = new_commits(commits, last_seen)
    if not fresh:
        return 0
    executor = [c for c in fresh if c["author"] == EXECUTOR_AUTHOR]
    if not executor:
        if not dry_run:
            write_state({"last_seen_sha": head, "updated_at": now_iso(), "last_event": "others-only"})
        return 0
    text = build_message(executor, history_gap=not found)
    key = f"b300-collab-{executor[0]['sha']}"
    if dry_run:
        log(f"dry-run {len(executor)} executor commit(s), key {key[:20]}…\n{text}")
        return 0
    url = webhook_url()
    if not url:
        log(f"no valid {WEBHOOK_ENV}; message logged only\n{text}")
        write_state({"last_seen_sha": head, "updated_at": now_iso(), "last_event": "logged-only"})
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
    log(f"sent {len(executor)} executor commit(s) up to {head[:7]}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true", help="只打印消息，不发送、不写状态")
    args = parser.parse_args()
    return run(dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
