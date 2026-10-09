#!/usr/bin/env python3
"""Opt-in bounded provider smoke checks using the deployed app adapters.

Run inside quant-research (or its configured Python environment). No database
writes, scans, signals or delivery calls. JSON lines contain no URLs or secrets.
"""

import argparse
import asyncio
from datetime import datetime, time, timezone
import json
import math
from pathlib import Path
import re
import sys
from time import monotonic
from urllib.request import urlopen
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "quant-service"))
sys.path.insert(0, "/app")
CN = ZoneInfo("Asia/Shanghai")


def row_time(row):
    for key in ("trade_time", "updated_at", "datetime", "price_observed_at", "trade_date", "date"):
        value = row.get(key)
        if not value:
            continue
        text = str(value).strip()
        for fmt in ("%Y%m%d%H%M%S", "%Y%m%d", "%Y-%m-%d"):
            try:
                stamp = datetime.strptime(text, fmt).replace(tzinfo=CN)
                if len(text) in (8, 10) and re.fullmatch(r"\d{4}", str(row.get("time") or "")):
                    clock = str(row["time"])
                    stamp = stamp.replace(hour=int(clock[:2]), minute=int(clock[2:]))
                return stamp
            except ValueError:
                pass
        try:
            stamp = datetime.fromisoformat(text.replace("Z", "+00:00"))
            return stamp.replace(tzinfo=CN) if stamp.tzinfo is None else stamp.astimezone(CN)
        except ValueError:
            pass
    return None


def summarize(rows, *, symbols, expected_date, now, session_active):
    stamps = [row_time(row) for row in rows]
    dates = sorted({stamp.date().isoformat() for stamp in stamps if stamp})
    identifiers = [str(r.get("ts_code") or r.get("symbol") or "") for r in rows]
    matched = set(identifiers) & set(symbols)
    bad_prices = 0
    for row in rows:
        try:
            value = float(row.get("price", row.get("close", float("nan"))))
            bad_prices += int(not math.isfinite(value) or value <= 0)
        except (TypeError, ValueError):
            bad_prices += 1
    missing_dates = sum(stamp is None for stamp in stamps)
    ages = [(now-stamp).total_seconds() for stamp in stamps if stamp]
    state = "healthy" if session_active else "available_off_session"
    if not rows:
        state = "empty"
    elif bad_prices or (symbols and len(matched) < len(symbols)):
        state = "partial"
    elif missing_dates:
        state = "unverified_time"
    elif dates != [expected_date] or any(age < -30 for age in ages):
        state = "stale"
    elif session_active and any(age > 120 for age in ages):
        state = "stale"
    return {"state": state, "rows": len(rows), "requested_symbols": len(symbols),
            "matched_symbols": len(matched), "missing_symbols": sorted(set(symbols)-matched),
            "trade_dates": dates, "missing_timestamp_rows": missing_dates, "invalid_price_rows": bad_prices,
            "latest_data_at": max((stamp for stamp in stamps if stamp), default=None),
            "decision_eligible": False}


async def main(args):
    from app.free_market_providers import tencent_order_book_quotes, tencent_intraday_minutes, sina_quotes, eastmoney_watch_flow_quotes
    from app.fuyao_provider import all_a_snapshot_rows, configured as fuyao_configured
    from app.longhu_vendor_source import intraday_source, configured as longhu_configured
    from app.http_clients import start_http_clients, close_http_clients

    symbols = list(dict.fromkeys(s.strip().upper() for s in args.symbols.split(",") if s.strip()))
    if not symbols:
        with urlopen(args.api_base.rstrip("/")+"/api/v1/intraday/watchlists", timeout=20) as response:
            symbols = [r["symbol"] for r in json.load(response)["items"] if r.get("enabled")]
    if not symbols or len(symbols) > 300 or any(not re.fullmatch(r"\d{6}\.(SH|SZ|BJ)", s) for s in symbols):
        raise ValueError("supply between 1 and 300 unique valid symbols")
    now = datetime.now(timezone.utc)
    local = now.astimezone(CN)
    expected = args.trade_date or local.date().isoformat()
    active = local.weekday() < 5 and (time(9,30) <= local.time() < time(11,30) or time(13) <= local.time() < time(15))
    # A live probe needs explicit calendar validation before claiming healthy.
    active = active and args.session_verified
    print(json.dumps({"probe_started_at": now.isoformat(), "expected_trade_date": expected,
                      "symbols": len(symbols), "session_verified": active, "live_effect": "none"}), flush=True)
    sample = symbols[:3]

    async def check(provider, capability, fn, selected, configured=True, price_required=True):
        start = monotonic()
        result = {"provider": provider, "capability": capability, "configured": configured}
        if not configured:
            result["state"] = "unconfigured"
        else:
            try:
                value = await asyncio.wait_for(fn(), timeout=45)
                rows, metadata = value if isinstance(value, tuple) else (value, {})
                result.update(summarize(rows, symbols=selected, expected_date=expected,
                                        now=datetime.now(timezone.utc), session_active=active))
                if not price_required:
                    result.update(state="responded_unverified_time" if rows else "empty", invalid_price_rows=None)
                result["transport_status"] = metadata.get("status")
                result["logical_limit"] = metadata.get("max_symbols")
                result["truncated"] = metadata.get("truncated")
            except Exception as error:
                # Never echo exception text: HTTP errors can embed keyed URLs.
                result.update(state="unavailable", error_type=type(error).__name__)
        result.update(latency_ms=round((monotonic()-start)*1000), checked_at=datetime.now(timezone.utc).isoformat())
        print(json.dumps(result, ensure_ascii=False, default=str), flush=True)

    await start_http_clients()
    try:
        if args.only in ("all", "longhu"):
            await check("longhuvip", "stock_quote", lambda: asyncio.to_thread(intraday_source().watch_quotes, symbols, max_symbols=300), symbols, longhu_configured())
            await check("longhuvip", "minute", lambda: asyncio.to_thread(intraday_source().stock_minutes, sample[0]), sample[:1], longhu_configured())
        if args.only == "all":
            await check("tencent_free", "stock_quote", lambda: tencent_order_book_quotes(sample, max_symbols=3), sample)
            await check("tencent_free", "minute", lambda: tencent_intraday_minutes(sample[0]), sample[:1])
            await check("sina_free", "stock_quote", lambda: sina_quotes(sample), sample)
            await check("eastmoney_free", "watchlist_flow_quote", lambda: eastmoney_watch_flow_quotes(sample, max_symbols=3), sample, price_required=False)
            await check("fuyao_ths", "all_a_snapshot", all_a_snapshot_rows, [], fuyao_configured())
    finally:
        await close_http_clients()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbols", default="", help="Explicit comma-separated basket, otherwise deployed watchlist")
    parser.add_argument("--api-base", default="http://127.0.0.1:8000")
    parser.add_argument("--trade-date", help="Expected exchange date, YYYY-MM-DD")
    parser.add_argument("--session-verified", action="store_true", help="Only after verifying persisted exchange calendar")
    parser.add_argument("--only", choices=("all", "longhu"), default="all")
    asyncio.run(main(parser.parse_args()))
