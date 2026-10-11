#!/usr/bin/env python3
"""Run the TDX adapters the way the intraday collectors would, cycle after cycle, and report what the hosts did.

One cycle is one round of the realtime readers, started together as the collector starts its sources: the all-A
snapshot (0x054b, about 70 pages on one connection), the index overview, the MAC batch quote and the MAC limit
prices of 100 symbols, the five-level book of 40, the board index quotes, 1-minute bars of ten symbols on the legacy
route and five on the MAC route, the session's ticks of three symbols, and the unusual-trade and top-board lists.
--stages names the cadences: ``name:cycle_seconds:duration_seconds``, where a cycle of 0 starts the next round as soon
as the last one is back. Per stage and adapter: calls, failures by error type, p50/p95/max latency of the answered
calls, the hosts that answered and the range of the row counts. Read-only; it prints one JSON document on stdout and
its progress on stderr, and writes nothing.

    python scripts/probe-tdx-realtime-load.py --stages realtime:30:300,fast:10:180,hot:0:30 --egress mac > out.json
"""

import argparse
import asyncio
import json
import math
import sys
import time
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path

if "__file__" in globals():     # piped to ``python -`` inside the owner container, the app package is already on the path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "quant-service"))
from app.datasources.sources import tdx_bars, tdx_legacy_misc, tdx_mac, tdx_microstructure, tdx_quotes, ticks  # noqa: E402

CALL_SECONDS = 25.0


def _host(warnings) -> str:
    return next((item.split("=", 1)[1] for item in warnings if str(item).startswith("tdx_host=")), "")


async def timed(name: str, call) -> dict:
    """One adapter call: what it returned or the type of what it raised, and how long it took."""
    started = time.monotonic()
    try:
        answer = await asyncio.wait_for(call(), CALL_SECONDS)
    except Exception as error:  # noqa: BLE001 - the error type is the measurement
        return {"adapter": name, "ok": False, "ms": round((time.monotonic() - started) * 1000),
                "error": f"{type(error).__name__}: {str(error)[:120]}"}
    rows, host = (answer if isinstance(answer, tuple) else (answer.rows, _host(answer.warnings)))
    return {"adapter": name, "ok": True, "ms": round((time.monotonic() - started) * 1000), "rows": len(rows), "host": host}


async def in_turn(name: str, calls) -> list[dict]:
    return [await timed(name, call) for call in calls]


async def cycle(stocks: list[str], boards: list[str], day: date) -> list[dict]:
    groups = await asyncio.gather(
        in_turn("all_a_snapshot", [tdx_legacy_misc.fetch_all_a_snapshot]),
        in_turn("index_overview", [tdx_legacy_misc.fetch_index_overview]),
        in_turn("mac_watch_snapshot_100", [lambda: tdx_mac.fetch_watch_snapshot(symbols=stocks[:100])]),
        in_turn("mac_limit_prices_100", [lambda: tdx_mac.fetch_limit_prices(symbols=stocks[:100])]),
        in_turn("order_book_40", [lambda: tdx_quotes.fetch_order_book(symbols=stocks[:40])]),
        in_turn("board_index_quote", [lambda: tdx_quotes.fetch_index_quote(symbols=boards)]),
        in_turn("minute_bars_legacy", [lambda s=s: tdx_bars.fetch_minute(symbol=s, count=240) for s in stocks[:10]]),
        in_turn("minute_bars_mac", [lambda s=s: tdx_mac.fetch_minute_bars(symbol=s, count=240) for s in stocks[10:15]]),
        in_turn("session_ticks", [lambda s=s: ticks.fetch_tdx_ticks(s, day) for s in stocks[:3]]),
        in_turn("unusual", [lambda: tdx_microstructure.fetch_unusual(market=0, start=0, count=50)]),
        in_turn("top_board", [tdx_microstructure.fetch_top_board]),
    )
    return [result for group in groups for result in group]


def percentile(values: list[int], share: float) -> int | None:
    return sorted(values)[min(len(values) - 1, math.ceil(share * len(values)) - 1)] if values else None


def summary(results: list[dict]) -> dict:
    by_adapter: dict[str, list[dict]] = {}
    for result in results:
        by_adapter.setdefault(result["adapter"], []).append(result)
    table = {}
    for name, items in by_adapter.items():
        answered = [item for item in items if item["ok"]]
        latencies = [item["ms"] for item in answered]
        rows = [item["rows"] for item in answered]
        table[name] = {
            "calls": len(items), "failed": len(items) - len(answered),
            "errors": dict(Counter(item["error"].split(":")[0] for item in items if not item["ok"])),
            "p50_ms": percentile(latencies, 0.5), "p95_ms": percentile(latencies, 0.95), "max_ms": max(latencies, default=None),
            "rows_min": min(rows, default=None), "rows_max": max(rows, default=None),
            "hosts": dict(Counter(item["host"] for item in answered)),
        }
    return table


async def stage(name: str, cycle_seconds: float, duration: float, stocks, boards, day) -> dict:
    began = time.monotonic()
    results, cycles, late = [], [], 0
    due = began
    while time.monotonic() - began < duration:
        await asyncio.sleep(max(0.0, due - time.monotonic()))
        started = time.monotonic()
        round_results = await cycle(stocks, boards, day)
        took = time.monotonic() - started
        results += [{**item, "cycle": len(cycles), "at": round(started - began, 1)} for item in round_results]
        cycles.append({"at": round(started - began, 1), "seconds": round(took, 2),
                       "failed": sum(not item["ok"] for item in round_results)})
        late += took > cycle_seconds > 0
        due = max(due + cycle_seconds, time.monotonic())
        print(f"{name} cycle {len(cycles)} at {cycles[-1]['at']}s took {took:.2f}s failed={cycles[-1]['failed']}",
              file=sys.stderr, flush=True)
    seconds = time.monotonic() - began
    return {"stage": name, "cycle_seconds": cycle_seconds, "seconds": round(seconds, 1), "cycles": len(cycles),
            "cycles_longer_than_cadence": late, "calls": len(results), "failed": sum(not item["ok"] for item in results),
            "calls_per_second": round(len(results) / seconds, 2),
            "cycle_seconds_p50": percentile([round(item["seconds"] * 1000) for item in cycles], 0.5),
            "cycle_seconds_max": max((round(item["seconds"] * 1000) for item in cycles), default=None),
            "adapters": summary(results), "cycle_log": cycles,
            "failures": [item for item in results if not item["ok"]][:60]}


async def main(args) -> dict:
    started = datetime.now(timezone.utc)
    snapshot = await tdx_legacy_misc.fetch_all_a_snapshot()
    symbols = [row["symbol"] for row in snapshot.rows if row["symbol"].startswith(("60", "00", "30"))]
    stocks = symbols[::max(1, len(symbols) // 100)][:100]
    catalog = await tdx_mac.fetch_board_catalog()
    boards = [f"{row['board_code']}.SH" for row in catalog.rows if str(row["board_code"]).startswith("88")][:20]
    day = date.fromisoformat(args.ticks_date)
    stages = []
    for spec in args.stages.split(","):
        name, cycle_seconds, duration = spec.split(":")
        stages.append(await stage(name, float(cycle_seconds), float(duration), stocks, boards, day))
    return {"schema": "tdx-realtime-load-v1", "egress": args.egress, "started_utc": started.isoformat(),
            "finished_utc": datetime.now(timezone.utc).isoformat(), "stocks": len(stocks), "boards": len(boards),
            "ticks_date": args.ticks_date, "legacy_hosts": len(tdx_legacy_misc.tdx_protocol.configured_hosts()),
            "mac_hosts": len(tdx_mac.configured_hosts()), "call_timeout_seconds": CALL_SECONDS, "stages": stages}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--stages", default="realtime:30:300,fast:10:180,hot:0:30")
    parser.add_argument("--egress", choices=("mac", "owner"), required=True)
    parser.add_argument("--ticks-date", required=True, help="the session whose ticks are read (ISO date)")
    print(json.dumps(asyncio.run(main(parser.parse_args())), ensure_ascii=False, indent=1))
