#!/usr/bin/env python3
"""owner 协作分支的飞书提醒：对方往 collab/owner-peer 推新提交，或分支历史被改写时发一条摘要。

双方都往 ``collab/owner-peer`` 提交，各自只需监听对方（见 docs/COLLAB_OWNER_PEER.md）。
supervisor 每 5 分钟跑一次本脚本：

* 把远端的协作分支取到私有引用 ``refs/collab-watch/collab``，不动本地检出，也不动
  大家共用的 ``origin/*`` 与 ``FETCH_HEAD``；
* 与上次看到的提交比较：只报作者不在"我方"名单里的新提交（我方默认 ``woshipapa``，
  ``--our-authors`` 或 ``OWNER_COLLAB_OUR_AUTHORS`` 可改，对方拿去用时填他们自己）；
* 上次看到的提交已不在分支上，说明有人强推改写了历史，发告警，提醒合并前先对齐。

发送复用 Paper-KB 的 ``notify.send_to_webhook``。webhook 只从环境变量
``OWNER_COLLAB_FEISHU_WEBHOOK_URL`` 读，不写盘、不进日志；没配置时只写日志。消息只带
提交号、标题和作者名，不带地址与邮箱。状态在 ``state/owner-collab-watch.json``：首次运行
只记基线、不发送；发送失败不推进状态，下一轮重试。``--dry-run`` 只打印。
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
REMOTE = "origin"
BRANCH = "collab/owner-peer"
WATCH_REF = "refs/collab-watch/collab"
BRANCH_URL = f"https://github.com/woshipapa/trading_hareness/commits/{BRANCH}"
STATE_PATH = Path(os.environ.get("OWNER_COLLAB_WATCH_STATE", str(ROOT / "state" / "owner-collab-watch.json")))
PAPER_KB = Path(os.environ.get("PAPER_KB_DIR", str(Path.home() / "codebase" / "literature_maps" / "paper_kb")))
WEBHOOK_ENV = "OWNER_COLLAB_FEISHU_WEBHOOK_URL"
WEBHOOK_PREFIX = "https://open.feishu.cn/open-apis/bot/v2/hook/"
OUR_AUTHORS = tuple(a.strip() for a in os.environ.get("OWNER_COLLAB_OUR_AUTHORS", "woshipapa").split(",") if a.strip())
LIMIT = 3500
SHOWN = 20


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def log(message: str) -> None:
    print(f"{now_iso()} owner-collab-watch: {message}", flush=True)


def git(*args: str, cwd: Path, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if check and result.returncode != 0:
        raise RuntimeError(f"git {args[0]} failed: {(result.stderr or result.stdout).strip()[:300]}")
    return result


def fetch_head(root: Path) -> str:
    git("fetch", "--quiet", REMOTE, f"+refs/heads/{BRANCH}:{WATCH_REF}", cwd=root)
    return git("rev-parse", WATCH_REF, cwd=root).stdout.strip()


def still_on_branch(sha: str, head: str, root: Path) -> bool:
    if git("cat-file", "-e", f"{sha}^{{commit}}", cwd=root, check=False).returncode != 0:
        return False
    return git("merge-base", "--is-ancestor", sha, head, cwd=root, check=False).returncode == 0


def commits(spec: Sequence[str], root: Path) -> list[dict[str, str]]:
    out = git("log", "--format=%H%x1f%an%x1f%s", *spec, cwd=root).stdout
    rows = [line.split("\x1f", 2) for line in out.splitlines() if line]
    return [{"sha": sha, "author": author, "subject": subject} for sha, author, subject in rows]


def render(theirs: list[dict[str, str]], *, rewritten_from: str | None, recent: list[dict[str, str]]) -> str:
    lines: list[str] = []
    if rewritten_from:
        lines.append(f"⚠️ 协作分支 {BRANCH} 的历史被改写（强推）：上次看到的 {rewritten_from[:7]} 已不在分支上。")
        lines.append("在双方对齐之前不要合并；collab_branch.py check 也会拦下。分支最近的提交：")
        shown = recent[:SHOWN]
    else:
        lines.append(f"协作分支 {BRANCH} 有对方的新提交（{len(theirs)}）：")
        shown = theirs[:SHOWN]
    lines += [f"- {c['sha'][:7]} {c['subject'][:160]}（{c['author']}）" for c in shown]
    hidden = (len(recent) if rewritten_from else len(theirs)) - len(shown)
    if hidden > 0:
        lines.append(f"……另有 {hidden} 个")
    lines.append(f"查看：{BRANCH_URL}")
    lines.append("合并进 main 前先跑：python3 scripts/collab_branch.py check")
    return "\n".join(lines)[:LIMIT]


def webhook_url() -> str:
    url = os.environ.get(WEBHOOK_ENV, "").strip()
    return url if url.startswith(WEBHOOK_PREFIX) and len(url) > len(WEBHOOK_PREFIX) else ""


def load_notify():
    sys.path.insert(0, str(PAPER_KB))
    import notify  # noqa: E402 - Paper-KB 的飞书传输层，按路径复用
    return notify


def read_state() -> dict[str, Any]:
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def write_state(state: dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = STATE_PATH.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, STATE_PATH)


def run(*, root: Path = ROOT, dry_run: bool = False, our_authors: Sequence[str] = OUR_AUTHORS,
        sender: Callable[[str, str, str], dict[str, Any]] | None = None) -> int:
    head = fetch_head(root)
    state = read_state()
    last = state.get("last_seen_sha")
    if not last:
        if not dry_run:
            write_state({"last_seen_sha": head, "updated_at": now_iso(), "last_event": "baseline"})
        log(f"baseline {head[:7]} (nothing sent)")
        return 0
    if head == last:
        log(f"no change at {head[:7]}")
        return 0
    rewritten = not still_on_branch(last, head, root)
    if rewritten:
        recent, theirs = commits(["-n", str(SHOWN + 5), head], root), []
    else:
        recent = []
        theirs = [c for c in commits([f"{last}..{head}"], root) if c["author"] not in our_authors]
    if not rewritten and not theirs:
        if not dry_run:
            write_state({"last_seen_sha": head, "updated_at": now_iso(), "last_event": "ours-only"})
        log(f"only our own commits up to {head[:7]}; nothing sent")
        return 0
    text = render(theirs, rewritten_from=last if rewritten else None, recent=recent)
    if dry_run:
        print(text)
        return 0
    event = "rewrite-alarm" if rewritten else "sent"
    url = webhook_url()
    if not url:
        log(f"no valid {WEBHOOK_ENV}; message logged only\n{text}")
        write_state({"last_seen_sha": head, "updated_at": now_iso(), "last_event": f"{event}-logged"})
        return 0
    try:
        result = (sender or load_notify().send_to_webhook)(text, url, f"owner-collab:{head}")
    except Exception as error:  # noqa: BLE001 - state stays, the next tick retries
        log(f"send error {type(error).__name__}; state kept at {last[:7]}")
        return 1
    if result.get("status") != "sent":
        log(f"send failed (status {result.get('status')!r}); state kept at {last[:7]}")
        return 1
    write_state({"last_seen_sha": head, "updated_at": now_iso(), "last_event": event})
    log(f"{event}: {len(theirs) or len(recent)} commit(s) up to {head[:7]}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--our-authors", default=",".join(OUR_AUTHORS),
                        help="comma-separated author names of this side; their commits are not reported")
    args = parser.parse_args(argv)
    try:
        return run(dry_run=args.dry_run, our_authors=[a.strip() for a in args.our_authors.split(",") if a.strip()])
    except RuntimeError as error:
        log(str(error))
        return 2


if __name__ == "__main__":
    sys.exit(main())
