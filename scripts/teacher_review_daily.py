#!/usr/bin/env python3
"""Daily teacher-review cycle driver: harness job dir -> peer watch pool.

Run on the workstation with the shared venv.  Every step reads or writes
files in the harness job directory and talks to the peer only through
``python -m app.teacher_review_ops`` inside its API container (see
docs/TEACHER_REVIEW_DAILY.md for the whole procedure):

    teacher_review_daily.py context JOB_DIR [--date 2026-09-22]
        -> JOB_DIR/daily_context_<T>.json + .md  (the closed session's settlement,
           plan lifecycle and the pool the next session starts with)
    teacher_review_daily.py check   JOB_DIR [--pack PATH]
        -> JOB_DIR/pack_check_<T>.json            (blocking problems, dry run, overrides)
    teacher_review_daily.py import  JOB_DIR [--pack PATH]
        -> JOB_DIR/import_report_<T>.json + pool_<T>.md
    teacher_review_daily.py outcome JOB_DIR [--date 2026-09-22] [--rerun]
        -> JOB_DIR/outcome_<T>.json + .md         (次日复盘：符合预期的、漏掉的大涨、卡在哪一条)
    teacher_review_daily.py digest  JOB_DIR [--date 2026-09-22]
        -> JOB_DIR/digest_<T>.json + .md          (跨策略日报：需要决定的、各策略记分牌、改动跟踪)
    teacher_review_daily.py sweep   JOB_DIR [--date 2026-09-22]
        -> JOB_DIR/sweep_<T>.json + .md           (反事实扫描：换一个阈值会多抓到什么、多放进来什么；收盘后跑)
    teacher_review_daily.py status  [JOB_DIR]

<T> is the review (trading) date as YYYYMMDD.  The default pack path is
JOB_DIR/teacher_pack_<T>.json.  Nothing here prints a secret: the write key
stays in the peer container's environment.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import teacher_harness as harness  # noqa: E402 - sibling module, after the path is set

PEER_HOST = os.environ.get("PEER_SSH_HOST", "stockpeer@47.110.79.189")
PEER_PORT = os.environ.get("PEER_SSH_PORT", "3535")
PEER_KEY = os.path.expanduser(os.environ.get("PEER_SSH_KEY", "~/.ssh/stockpeer_ed25519"))
PEER_CONTAINER = os.environ.get("PEER_API_CONTAINER", "trading-hareness-peer-quant-research-1")
PEER_APP_DIR = os.environ.get("PEER_APP_DIR", "/app/hotfix/current")
CN = timezone(timedelta(hours=8))
REQUIRED_HARNESS_FILES = ("transcript_large.srt", "provenance.json", "state.json")


def peer_ops(command: list[str], stdin: bytes | None = None, timeout: int = 2400) -> dict:
    """Run one ops command in the peer API container and return its JSON."""
    for token in [PEER_CONTAINER, PEER_APP_DIR, *command]:
        if not re.fullmatch(r"[A-Za-z0-9_./:=-]+", token):
            raise SystemExit(f"refusing unsafe token: {token!r}")
    remote = ("export XDG_RUNTIME_DIR=/run/user/$(id -u); export DOCKER_HOST=unix://$XDG_RUNTIME_DIR/docker.sock; "
              f"docker exec -i -w {PEER_APP_DIR} -e PYTHONPATH={PEER_APP_DIR} -e QUANT_BACKGROUND_TASKS_ENABLED=false "
              f"{PEER_CONTAINER} python -m app.teacher_review_ops {' '.join(command)}")
    completed = subprocess.run(
        ["ssh", "-i", PEER_KEY, "-p", PEER_PORT, "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes", PEER_HOST, remote],
        input=stdin, capture_output=True, timeout=timeout)
    lines = [line for line in completed.stdout.decode("utf-8", "replace").splitlines() if line.startswith("{")]
    if not lines:
        tail = completed.stderr.decode("utf-8", "replace")[-1500:]
        raise SystemExit(f"peer ops {command[0]} returned no JSON (exit {completed.returncode}):\n{tail}")
    return json.loads(lines[-1])


def review_date_of(job: pathlib.Path, explicit: date | None, pack_path: pathlib.Path | None = None) -> date:
    if explicit:
        return explicit
    if pack_path and pack_path.exists():
        return date.fromisoformat(json.loads(pack_path.read_text(encoding="utf-8"))["review_date"])
    for path in sorted(job.glob("teacher_pack_*.json")) + sorted(job.glob("owner_market_evidence_*.json")):
        match = re.search(r"_(\d{8})\.json$", path.name)
        if match:
            return datetime.strptime(match.group(1), "%Y%m%d").date()
    raise SystemExit("cannot tell the review date: pass --date or add teacher_pack_<T>.json / owner_market_evidence_<T>.json")


def write(path: pathlib.Path, value) -> None:
    path.write_text(value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n",
                    encoding="utf-8")
    print(f"wrote {path}")


def pool_markdown(pool: list[dict], title: str) -> str:
    labels = {"new": "新计划（推送）", "promoted": "晋级延续（推送）", "observe": "观察（不推送买点）"}
    lines = [f"# {title}", ""]
    for state in ("new", "promoted", "observe"):
        items = [item for item in pool if item["state"] == state]
        lines.append(f"## {labels[state]} · {len(items)} 只")
        lines += [f"- {item['name']}（{item['symbol']}）{item['playbook']}｜关键价 {item['key_level'] or '—'}｜"
                  f"{item['reason'] or item['setup'] or ''}" for item in items]
        lines.append("")
    return "\n".join(lines)


def in_session_now(now: datetime) -> bool:
    """现在是不是真的盘中。

    以前写的是 ``now.weekday() < 5``：休市的工作日（中秋、国庆）也被当成交易日，
    于是 09:15–15:00 之间 sweep 一律被拒，而每 20 分钟一轮的自动化就这么空转半天。
    交易日历是 exchange_calendars 的 XSHG（teacher_harness.is_trading_day），拿不到才退回工作日判断。
    """
    if not ((9, 15) <= (now.hour, now.minute) < (15, 0)):
        return False
    return harness.is_trading_day(now.date())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=("context", "check", "import", "outcome", "digest", "sweep", "status"))
    parser.add_argument("job_dir", nargs="?", type=pathlib.Path)
    parser.add_argument("--date", type=date.fromisoformat, help="review (trading) date, default from the job files")
    parser.add_argument("--pack", type=pathlib.Path)
    parser.add_argument("--rerun", action="store_true", help="outcome: recompute instead of reading the archive")
    args = parser.parse_args()

    if args.command == "status":
        pool = peer_ops(["status"])["pool"]
        text = pool_markdown(pool, f"老师计划观察池 · {datetime.now(CN):%Y-%m-%d %H:%M}")
        if args.job_dir:
            write(args.job_dir / f"pool_{datetime.now(CN):%Y%m%d_%H%M}.md", text)
        print(text)
        return

    if args.job_dir is None or not args.job_dir.is_dir():
        raise SystemExit("JOB_DIR (a harness job directory) is required")
    job = args.job_dir.resolve()
    missing = [name for name in REQUIRED_HARNESS_FILES if not (job / name).exists()]
    if missing:
        raise SystemExit(f"harness job is incomplete, missing: {', '.join(missing)}")

    if args.command in {"digest", "sweep"}:
        trade_date = review_date_of(job, args.date)
        now = datetime.now(CN)
        if args.command == "sweep" and in_session_now(now):
            raise SystemExit("sweep replays a whole session of scan inputs; run it after the close, not during it")
        result = peer_ops([args.command, f"--date={trade_date.isoformat()}"], timeout=1800)
        if result.get("status") not in {"ok", "completed"}:
            raise SystemExit(f"{args.command} unavailable: {result.get('reason') or result.get('status')}")
        stem = trade_date.strftime("%Y%m%d")
        if args.command == "digest":
            write(job / f"digest_{stem}.json", result.get("digest") or {})
            write(job / f"digest_{stem}.md", result.get("markdown") or "")
            print("\n".join((result.get("digest") or {}).get("decisions") or ["（今天没有需要决定的事项）"]))
        else:
            write(job / f"sweep_{stem}.json", result)
            page = [f"# 反事实阈值扫描 {trade_date.isoformat()}", "",
                    f"重放 {result.get('symbols')} 只未触发的票，当日中位数 {result.get('benchmark_session_pct')}。"
                    "每个变体只改一个阈值；收益为扣一次往返成本的净值，封板价触发计为买不到。", ""]
            page += [f"- {line}" for line in result.get("lines") or []]
            page += ["", "看起来更好的变体只是赢得了一次**预注册改动**的资格：先把它连同预期写进变更台账，再改。"]
            write(job / f"sweep_{stem}.md", "\n".join(page) + "\n")
            print("\n".join(result.get("lines") or ["（参与的票太少，没有可报告的变体）"]))
        return

    if args.command == "outcome":
        trade_date = review_date_of(job, args.date)
        command = ["outcome", f"--date={trade_date.isoformat()}"] + (["--rerun"] if args.rerun else [])
        result = peer_ops(command, timeout=900)
        if result.get("status") != "ok":
            raise SystemExit(f"outcome review unavailable: {result.get('reason') or result.get('status')}")
        stem = trade_date.strftime("%Y%m%d")
        write(job / f"outcome_{stem}.json", result["report"])
        write(job / f"outcome_{stem}.md", result["markdown"])
        counts = (result["report"] or {}).get("counts") or {}
        print(json.dumps(counts, ensure_ascii=False, indent=2))
        return

    if args.command == "context":
        trade_date = review_date_of(job, args.date)
        result = peer_ops(["context", f"--date={trade_date.isoformat()}"])
        stem = trade_date.strftime("%Y%m%d")
        write(job / f"daily_context_{stem}.json", result["context"])
        write(job / f"daily_context_{stem}.md", result["markdown"])
        stages = result["context"]["peer_close_stages"]
        if any(status != "completed" for status in stages.values()):
            print(f"warning: peer close stages not all completed: {stages}", file=sys.stderr)
        return

    trade_date = review_date_of(job, args.date, args.pack)
    stem = trade_date.strftime("%Y%m%d")
    pack_path = args.pack or job / f"teacher_pack_{stem}.json"
    if not pack_path.exists():
        raise SystemExit(f"no pack at {pack_path}; build it first (docs/teacher_review/PACK_BUILD_BRIEF.md)")
    body = pack_path.read_bytes()

    if args.command == "check":
        report = peer_ops(["check", "--pack=-"], stdin=body, timeout=900)
        write(job / f"pack_check_{stem}.json", report)
        print(json.dumps({key: report[key] for key in ("ok", "problems", "warnings", "counts", "dry_run")},
                         ensure_ascii=False, indent=2, default=str))
        sys.exit(0 if report["ok"] else 1)

    now = datetime.now(CN)
    if in_session_now(now):
        print("note: importing during the session - plans take effect from the next session", file=sys.stderr)
    result = peer_ops(["import", "--pack=-"], stdin=body, timeout=2400)
    write(job / f"import_report_{stem}.json", result)
    if not result["check"]["ok"]:
        print(json.dumps(result["check"]["problems"], ensure_ascii=False, indent=2))
        sys.exit(1)
    pool = peer_ops(["status"])["pool"]
    write(job / f"pool_{stem}.md", pool_markdown(pool, f"导入 {pack_path.name} 后的老师计划观察池"))
    imported = result["import"]
    print(json.dumps({key: imported.get(key) for key in ("status", "session_date", "planned", "plan_failures",
                                                          "retired_by_newer_review", "transport")},
                     ensure_ascii=False, indent=2, default=str))
    sys.exit(0 if imported.get("status") == "imported" else 1)


if __name__ == "__main__":
    main()
