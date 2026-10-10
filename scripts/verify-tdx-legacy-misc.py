#!/usr/bin/env python3
"""Probe legacy TDX commands and emit bounded, read-only JSON evidence."""

from __future__ import annotations

import argparse
import json
import socket
import struct
import sys
import time
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "quant-service"))

from app.datasources.sources import tdx_legacy_misc as legacy  # noqa: E402
from app.datasources.sources import tdx_protocol  # noqa: E402

HOSTS = (("60.191.117.167", 7709), ("218.75.126.9", 7709), ("117.34.114.13", 7709))
SYMBOLS = ((0, "000001"), (1, "600519"), (0, "399300"))
PROBE_TRADE_DATE = date(2026, 10, 9)
PAGE_SIZE = 80
MAX_RANKING_PAGES = 100
COMMAND_ERRORS = (OSError, socket.timeout, tdx_protocol.TdxProtocolError, ValueError, IndexError, struct.error)

# Values with names are from gotdx.  4 and 9 are deliberately labelled as
# probes: their server-side meaning varies by desktop release and is recorded,
# not assumed, unless the returned codes make it evident.
CATEGORY_PROBES = {
    "sh_a": (0, 0), "sz_a": (2, 0), "all_a": (6, 0), "chinext": (14, 0),
    "star": (8, 0), "bj": (12, 0), "st_filter": (6, 4), "boards_probe": (4, 0),
    "funds_probe": (9, 0),
}


def _run(result: dict, name: str, fn) -> object | None:
    started = time.perf_counter()
    try:
        value = fn()
    except COMMAND_ERRORS as exc:
        result["commands"][name] = {"ok": False, "error": f"{type(exc).__name__}: {exc}",
                                     "elapsed_ms": round((time.perf_counter() - started) * 1000, 2)}
        return None
    result["commands"][name] = {"ok": True, "elapsed_ms": round((time.perf_counter() - started) * 1000, 2)}
    return value


def _ranking(client: legacy.LegacyMiscClient, category: int, filter_value: int = 0) -> dict:
    rows: list[dict] = []
    pages = 0
    complete = False
    for start in range(0, PAGE_SIZE * MAX_RANKING_PAGES, PAGE_SIZE):
        page = client.quotes_list(category=category, sort_type=legacy.QUOTE_SORT_TYPES["change_pct"],
                                   start=start, count=PAGE_SIZE, sort_reverse=True, filter=filter_value)
        pages += 1
        rows.extend(page)
        if len(page) < PAGE_SIZE:
            complete = True
            break
    return {"rows": len(rows), "pages": pages, "page_size": PAGE_SIZE, "complete": complete,
            "first_codes": [f"{row['market']}:{row['code']}" for row in rows[:5]],
            "rows_for_sentiment": {"advancers": sum(row["price"] > row["pre_close"] for row in rows),
                                   "decliners": sum(row["price"] < row["pre_close"] for row in rows),
                                   "unchanged": sum(row["price"] == row["pre_close"] for row in rows)}}


def _probe_host(host: str, port: int, timeout: float) -> dict:
    result: dict = {"host": f"{host}:{port}", "commands": {}, "errors": []}
    started = time.perf_counter()
    try:
        with legacy.LegacyMiscClient(host, port, timeout) as client:
            ping = _run(result, "0x0015", lambda: client.raw_command(legacy.KMSG_PING, packet_type=0))
            if ping is not None:
                result["commands"]["0x0015"].update({"raw_length": len(ping), "text": legacy.parse_ping(ping)["text"][:80]})

            # These small informational requests are answered before an
            # unsupported experimental command can close the session.
            for name, opcode in (("0x0002", legacy.KMSG_EXCHANGEANNOUNCE), ("0x0004", legacy.KMSG_HEARTBEAT),
                                 ("0x000a", legacy.KMSG_ANNOUNCEMENT)):
                raw = _run(result, name, lambda op=opcode: client.raw_command(op))
                if raw is not None:
                    result["commands"][name].update({"raw_length": len(raw), "text": legacy.parse_raw(raw)["text"][:80]})

            ranking_values: dict[str, dict] = {}
            for label, (category, filter_value) in CATEGORY_PROBES.items():
                value = _run(result, f"0x054b:{label}", lambda c=category, f=filter_value: _ranking(client, c, f))
                if value is not None:
                    ranking_values[label] = value
            result["ranking"] = ranking_values

            batch_sizes = {}
            for requested in (80, 200, 500):
                stocks = (SYMBOLS * ((requested + len(SYMBOLS) - 1) // len(SYMBOLS)))[:requested]
                value = _run(result, f"0x054c:{requested}", lambda s=stocks: client.quotes_batch(s))
                if value is not None:
                    batch_sizes[str(requested)] = {"requested": requested, "rows": len(value),
                                                   "codes": [f"{row['market']}:{row['code']}" for row in value[:5]]}
            result["batch_ceiling"] = batch_sizes

            all_a = ranking_values.get("all_a", {})
            top = [(int(market), code) for market, code in (value.split(":", 1) for value in all_a.get("first_codes", []))]
            top_batch = _run(result, "0x054c:top5_crosscheck", lambda: client.quotes_batch(top)) if top else None
            if top_batch is not None:
                result["top5_crosscheck"] = {"ranking": all_a.get("first_codes", []),
                                              "batch": [f"{r['market']}:{r['code']}" for r in top_batch],
                                              "match": {f"{r['market']}:{r['code']}" for r in top_batch} == set(all_a.get("first_codes", []))}

            index = _run(result, "0x051d", lambda: client.index_info(1, "000001"))
            if index:
                result["commands"]["0x051d"].update({"rows": 1, "fields": sorted(index),
                                                       "up_count": index.get("up_count"), "down_count": index.get("down_count")})
            momentum = _run(result, "0x051c", lambda: client.index_momentum(1, "000001"))
            if momentum is not None:
                result["commands"]["0x051c"].update({"rows": len(momentum), "last": momentum[-1] if momentum else None})

            current = _run(result, "0x0fc5", lambda: client.transaction_data(0, "000001", count=20))
            if current is not None:
                result["commands"]["0x0fc5"].update({"rows": len(current), "directions": sorted({r["direction"] for r in current}),
                                                       "actions": sorted({r["action"] for r in current})})
            historical = _run(result, "0x0fc6", lambda: client.transaction_data_trans(0, "000001", PROBE_TRADE_DATE, count=20))
            if historical is not None:
                result["commands"]["0x0fc6"].update({"rows": len(historical), "directions": sorted({r["direction"] for r in historical}),
                                                       "actions": sorted({r["action"] for r in historical})})
            old_history = _run(result, "0x0fb5", lambda: client.history_transaction_data(0, "000001", PROBE_TRADE_DATE, count=20, with_direction=False))
            if old_history is not None:
                result["commands"]["0x0fb5"].update({"rows": len(old_history)})

            for name, fn in {
                "0x0fd1": lambda: client.chart_sampling(0, "000001"),
                "0x0452": lambda: client.security_feature452(0, 20),
                "0x052d": lambda: client.bars_offset(9, 0, "000001", 0, 5),
                "0x0547": lambda: client.quotes_encrypted(SYMBOLS),
            }.items():
                value = _run(result, name, fn)
                if value is not None:
                    result["commands"][name]["rows"] = len(value.get("prices", [])) if isinstance(value, dict) else len(value)

            for name, opcode in (("0x000b", legacy.KMSG_TODOB), ("0x0fde", legacy.KMSG_TODOFDE)):
                raw = _run(result, name, lambda op=opcode: client.raw_command(op))
                if raw is not None:
                    result["commands"][name].update({"raw_length": len(raw), "text": legacy.parse_raw(raw)["text"][:80]})

    except COMMAND_ERRORS as exc:
        result["errors"].append(f"setup: {type(exc).__name__}: {exc}")
    result["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 2)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--timeout", type=float, default=5.0)
    args = parser.parse_args()
    reports = [_probe_host(host, port, args.timeout) for host, port in HOSTS]
    for report in reports:
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    usable = [r for r in reports if r.get("ranking", {}).get("all_a", {}).get("rows", 0)
              and r.get("batch_ceiling", {}).get("80", {}).get("rows", 0)]
    return 0 if usable else 1


if __name__ == "__main__":
    raise SystemExit(main())
