#!/usr/bin/env python3
"""Export a bound group's messages for hand-off to an AI, with a resume cursor.

The edge bridge archives every observed message of every type and exposes it
through the adapter's export proxy (reached over the feishu tunnel at
127.0.0.1:18300).  This tool drives that proxy incrementally: it keeps a small
per-chat state file recording the last exported sequence cursor and the time
range each export covered, so each run writes only what is new since the last
run and you always know "last export covered 10-01 09:00 .. 10-09 20:00".

It needs no secret — the adapter injects the bridge token server-side.

Examples:
  # incremental transcript for one group (only what's new since last run)
  python3 export-group-history.py --chat 7672808367722679283
  # a fixed recent window, as raw NDJSON
  python3 export-group-history.py --chat 7672808367722679283 --mode days --days 7 --format ndjson
  # show what range was last exported for every tracked group
  python3 export-group-history.py --list
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_STATE = REPO_ROOT / "state" / "feishu-export-state.json"
DEFAULT_OUT = REPO_ROOT / "state" / "feishu-exports"
DEFAULT_ADAPTER = "http://127.0.0.1:18300"
EXPORT_PATH = "/api/group-relay/larkagentx/history/export"


def load_state(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"chats": {}}


def save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def iso(epoch: float | None) -> str | None:
    if not epoch:
        return None
    return dt.datetime.fromtimestamp(float(epoch), dt.timezone(dt.timedelta(hours=8))).isoformat()


def parse_float_header(value: str | None) -> float | None:
    try:
        return float(value) if value else None
    except (TypeError, ValueError):
        return None


def fetch_export(adapter: str, chat_id: str, *, fmt: str, after_sequence: int | None,
                 days: int | None) -> tuple[bytes, dict[str, str]]:
    params = {"chat_id": chat_id, "format": fmt}
    if after_sequence is not None:
        params["after_sequence"] = str(after_sequence)
    else:
        params["days"] = str(days or 1)
    url = adapter.rstrip("/") + EXPORT_PATH + "?" + urllib.parse.urlencode(params)
    request = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            body = response.read()
            headers = {k.lower(): v for k, v in response.headers.items()}
            return body, headers
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")[:400]
        raise SystemExit(f"导出失败 chat={chat_id} (HTTP {error.code}): {detail}") from error
    except OSError as error:
        raise SystemExit(f"连不上 adapter {adapter}（feishu 隧道是否在？）: {error}") from error


def export_one(adapter: str, chat_id: str, state: dict, out_dir: Path, *,
               fmt: str, mode: str, days: int) -> dict:
    chats = state.setdefault("chats", {})
    prior = chats.get(chat_id, {})
    if mode == "incremental":
        after_sequence: int | None = int(prior.get("next_sequence", 0) or 0)
        window_days = None
    elif mode == "full":
        after_sequence, window_days = 0, None
    else:  # days
        after_sequence, window_days = None, days

    body, headers = fetch_export(adapter, chat_id, fmt=fmt,
                                 after_sequence=after_sequence, days=window_days)
    count = int(headers.get("x-larkagentx-event-count", "0") or 0)
    next_sequence = int(headers.get("x-larkagentx-next-sequence", prior.get("next_sequence", 0)) or 0)
    from_epoch = parse_float_header(headers.get("x-larkagentx-from-time"))
    to_epoch = parse_float_header(headers.get("x-larkagentx-to-time"))

    file_path = None
    if count > 0 and body.strip():
        out_dir.mkdir(parents=True, exist_ok=True)
        ext = "txt" if fmt == "transcript" else "jsonl"
        stamp = dt.datetime.now().strftime("%Y%m%dT%H%M%S")
        file_path = out_dir / f"{chat_id}-{stamp}-{mode}.{ext}"
        file_path.write_text(body.decode("utf-8", errors="replace"), encoding="utf-8")

    record = {
        "next_sequence": next_sequence,
        "last_export_at": dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).isoformat(),
        "last_event_count": count,
        "last_range": {"from_epoch": from_epoch, "to_epoch": to_epoch,
                       "from": iso(from_epoch), "to": iso(to_epoch)},
        "cumulative_events": int(prior.get("cumulative_events", 0)) + count,
        "last_file": str(file_path) if file_path else prior.get("last_file"),
        "last_format": fmt,
    }
    # Incremental runs advance the cursor; a fixed-window (days) peek must not
    # move it, or it would skip messages the next incremental run should catch.
    if mode != "incremental":
        record["next_sequence"] = int(prior.get("next_sequence", 0) or 0)
    chats[chat_id] = record
    return {"chat_id": chat_id, "count": count, "file": file_path, "range": record["last_range"],
            "cursor": record["next_sequence"]}


def listed_chats(adapter: str) -> list[str]:
    url = adapter.rstrip("/") + "/api/group-relay/status"
    try:
        with urllib.request.urlopen(url, timeout=15) as response:
            data = json.load(response)
    except OSError as error:
        raise SystemExit(f"读取监听群列表失败: {error}") from error
    larkx = data.get("larkagentx") or {}
    ids = larkx.get("websocket_chat_ids") or larkx.get("listen_chat_ids") or []
    return [str(c) for c in ids if str(c).isdigit()]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--adapter", default=DEFAULT_ADAPTER)
    parser.add_argument("--chat", action="append", default=[], help="数字 chat_id（可多次）")
    parser.add_argument("--all-listened", action="store_true", help="导出所有正在监听的数字群")
    parser.add_argument("--format", choices=["transcript", "ndjson"], default="transcript")
    parser.add_argument("--mode", choices=["incremental", "days", "full"], default="incremental")
    parser.add_argument("--days", type=int, default=1, choices=[1, 7, 30])
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--state", default=str(DEFAULT_STATE))
    parser.add_argument("--list", action="store_true", help="只打印每个群上次导出的范围，不导出")
    args = parser.parse_args()

    state_path = Path(args.state).expanduser()
    state = load_state(state_path)

    if args.list:
        chats = state.get("chats", {})
        if not chats:
            print("（还没有任何导出记录）")
            return 0
        for chat_id, rec in sorted(chats.items()):
            rng = rec.get("last_range", {})
            print(f"{chat_id}: 上次导出 {rec.get('last_export_at')} | 覆盖 {rng.get('from')} .. {rng.get('to')} "
                  f"| {rec.get('last_event_count')} 条 | 游标 seq={rec.get('next_sequence')} "
                  f"| 累计 {rec.get('cumulative_events')} | {rec.get('last_file') or '无文件'}")
        return 0

    chats = list(dict.fromkeys(args.chat)) or (listed_chats(args.adapter) if args.all_listened else [])
    if not chats:
        raise SystemExit("请用 --chat <id>（可多次）或 --all-listened 指定要导出的群")
    for chat_id in chats:
        if not chat_id.isdigit():
            print(f"跳过非数字 chat_id: {chat_id}")
            continue
        result = export_one(args.adapter, chat_id, state, Path(args.out).expanduser(),
                            fmt=args.format, mode=args.mode, days=args.days)
        save_state(state_path, state)  # persist after each chat so a mid-run failure keeps progress
        rng = result["range"]
        if result["count"] > 0:
            print(f"{chat_id}: 导出 {result['count']} 条，覆盖 {rng.get('from')} .. {rng.get('to')} "
                  f"→ {result['file']}  （新游标 seq={result['cursor']}）")
        else:
            print(f"{chat_id}: 没有新消息（游标 seq={result['cursor']} 未变）")
    print(f"状态已记录于 {state_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
