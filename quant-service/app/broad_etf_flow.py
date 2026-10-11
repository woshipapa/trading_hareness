"""The national team's footprint: turnover of a fixed basket of broad-index ETFs (decision 0013).

The post-close stage ``broad_etf_flow`` does three things:
- fetches the basket's daily bars for the last ``FETCH_DAYS`` calendar days;
- stores each bar as a ``broad_etf_daily`` observation;
- stores one ``broad_etf_flow_daily`` reading per session: the basket turnover
  over its mean of the previous ``BASE_SESSIONS`` sessions, both taken over the
  ETFs present on all those days.

Sources, in the platform's order:
- Fuyao ``fund_market_historical`` reports turnover exactly.
- For an ETF Fuyao cannot serve, Tencent's public day K-line. It reports
  volume only, so turnover is estimated as volume x the bar's mean price
  (0.1% from Fuyao's on 2026-09-21) and flagged ``amount_estimated``.
- Longhu has no fund bars, Eastmoney refused its kline endpoint from our
  network on 2026-10-09, and Sina's needs script decoding, so none of the three
  is used.

In the 2025-04..2026-10 backtest, cold days (temperature <= 40) with a ratio of
1.5 or more rose over the next 1, 3 and 5 days 90% of the time. That is ten
days in four or five episodes. Research only.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Awaitable, Callable
from datetime import date, datetime, timedelta
from typing import Any

from . import derived_daily_readings
from .derived_daily_readings import CN_TZ, session_close
from .public_market_repository import persist_timed_observations

BASKET: tuple[tuple[str, str], ...] = (
    ("510300.SH", "沪深300"), ("510310.SH", "沪深300"), ("510330.SH", "沪深300"), ("159919.SZ", "沪深300"),
    ("510050.SH", "上证50"), ("510500.SH", "中证500"), ("512100.SH", "中证1000"), ("159915.SZ", "创业板"),
    ("588000.SH", "科创50"), ("512050.SH", "A500"), ("159338.SZ", "A500"), ("563360.SH", "A500"),
)
BAR_CAPABILITY = "broad_etf_daily"
FLOW_CAPABILITY = "broad_etf_flow_daily"
VERSION = "broad-etf-flow-v1"
BASE_SESSIONS = 20
SURGE_RATIO = 1.5
MIN_CODES = 9                # of 12; fewer and the session has no ratio
FETCH_DAYS = 60              # calendar days: the 20-session base plus the sessions re-stored
KEEP_SESSIONS = 10
TENCENT_KLINE_URL = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"

Fetch = Callable[[str, date, date], Awaitable[list[dict[str, Any]]]]


def _ms(moment: datetime) -> int:
    return int(moment.timestamp() * 1000)


async def fuyao_bars(code: str, start: date, end: date) -> list[dict[str, Any]]:
    from .fuyao_provider import fetch_envelope
    envelope = await fetch_envelope("fund_market_historical", {
        "thscode": code, "interval": "1d",
        "start": _ms(datetime.combine(start, datetime.min.time(), CN_TZ)), "end": _ms(session_close(end)),
    })
    rows = []
    for item in (envelope.get("data") or {}).get("item") or []:
        if not item.get("date_ms") or not item.get("turnover"):
            continue
        rows.append({
            "trade_date": datetime.fromtimestamp(item["date_ms"] / 1000, CN_TZ).date().isoformat(), "ts_code": code,
            "amount_cny": float(item["turnover"]), "volume_shares": float(item.get("volume") or 0),
            "close": item.get("close_price"), "amount_estimated": False, "source": "fuyao_ths",
        })
    return rows


async def tencent_bars(code: str, start: date, end: date) -> list[dict[str, Any]]:
    from .datasources.http import request_json
    symbol = code[-2:].lower() + code[:6]
    payload = await request_json("GET", TENCENT_KLINE_URL, params={
        "param": f"{symbol},day,{start.isoformat()},{end.isoformat()},{(end - start).days + 10},",
    })
    rows = []
    for bar in ((payload.get("data") or {}).get(symbol) or {}).get("day") or []:
        day, prices, lots = bar[0], [float(value) for value in bar[1:5]], float(bar[5])
        if lots <= 0:
            continue
        rows.append({
            "trade_date": day, "ts_code": code, "amount_cny": lots * 100 * sum(prices) / 4,
            "volume_shares": lots * 100, "close": prices[1], "amount_estimated": True, "source": "tencent_free",
        })
    return rows


async def fetch_basket(start: date, end: date, *, primary: Fetch = fuyao_bars,
                       fallback: Fetch = tencent_bars) -> dict[str, Any]:
    """Every basket ETF's bars, from Fuyao or else Tencent; an ETF neither serves is listed in ``errors``."""
    bars: dict[str, list[dict[str, Any]]] = {}
    errors: dict[str, str] = {}
    for code, _label in BASKET:
        failures = []
        for fetch in (primary, fallback):
            try:
                rows = await fetch(code, start, end)
            except Exception as error:  # noqa: BLE001 - one ETF's failure must not stop the basket
                failures.append(f"{getattr(fetch, '__name__', 'source')}: {type(error).__name__}")
                continue
            if rows:
                bars[code] = rows
                break
            failures.append(f"{getattr(fetch, '__name__', 'source')}: no rows")
        if code not in bars:
            errors[code] = "; ".join(failures)
    return {"bars": bars, "errors": errors}


def flow_readings(bars: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """One reading per session after the first ``BASE_SESSIONS``; the ratio is None with fewer than ``MIN_CODES`` ETFs."""
    amounts = {code: {row["trade_date"]: row["amount_cny"] for row in rows if row.get("amount_cny")}
               for code, rows in bars.items()}
    estimated = {code: {row["trade_date"] for row in rows if row.get("amount_estimated")} for code, rows in bars.items()}
    sessions = sorted({day for per_code in amounts.values() for day in per_code})
    readings = []
    for index in range(BASE_SESSIONS, len(sessions)):
        day, window = sessions[index], sessions[index - BASE_SESSIONS:index]
        codes = sorted(code for code, per_code in amounts.items() if day in per_code and all(d in per_code for d in window))
        reading: dict[str, Any] = {"trade_date": day, "codes": len(codes), "ratio": None, "surge": None,
                                   "basket_turnover_cny": None, "base_turnover_cny": None, "amount_estimated": None}
        if len(codes) >= MIN_CODES:
            today = sum(amounts[code][day] for code in codes)
            base = sum(amounts[code][d] for code in codes for d in window) / BASE_SESSIONS
            ratio = today / base
            reading.update({"ratio": round(ratio, 4), "surge": ratio >= SURGE_RATIO, "basket_turnover_cny": round(today),
                            "base_turnover_cny": round(base),
                            "amount_estimated": any(day in estimated[code] for code in codes)})
        readings.append(reading)
    return readings


def persist_bars(database: Any, bars: dict[str, list[dict[str, Any]]]) -> int:
    """Store each bar under its own source; a bar identical to a stored one is ignored by the unique key."""
    by_source: dict[str, list[dict[str, Any]]] = {}
    for rows in bars.values():
        for row in rows:
            by_source.setdefault(row["source"], []).append(
                {**row, "effective_at": session_close(date.fromisoformat(row["trade_date"])).isoformat()})
    return sum(persist_timed_observations(database, source, BAR_CAPABILITY, rows) for source, rows in by_source.items())


async def refresh(database: Any, trade_date: date, *, run_database: Callable[..., Awaitable[Any]],
                  fetch: Callable[[date, date], Awaitable[dict[str, Any]]] | None = None,
                  keep: int = KEEP_SESSIONS, apply: bool = True) -> dict[str, Any]:
    """Fetch, store and derive; ``completed`` only when the session itself has a ratio."""
    fetched = await (fetch or fetch_basket)(trade_date - timedelta(days=FETCH_DAYS), trade_date)
    bars = fetched["bars"]
    readings = [{**reading, "version": VERSION, "research_only": True, "live_effect": "none"}
                for reading in flow_readings(bars) if reading["trade_date"] <= trade_date.isoformat()][-keep:]
    stored_bars, counts = 0, {"stored": 0, "unchanged": 0}
    if apply:
        stored_bars = await run_database(persist_bars, database, bars, timeout_seconds=60)
        counts = await run_database(derived_daily_readings.store, database, FLOW_CAPABILITY, readings, timeout_seconds=60)
    latest = readings[-1] if readings else None
    completed = bool(latest and latest["trade_date"] == trade_date.isoformat() and latest["ratio"] is not None)
    sources = sorted({row["source"] for rows in bars.values() for row in rows})
    return {
        "status": "completed" if completed else "blocked", "trade_date": trade_date.isoformat(),
        "etfs": len(bars), "sources": sources, "errors": fetched["errors"], "bars_written": stored_bars, **counts,
        "latest": {key: latest[key] for key in ("trade_date", "ratio", "surge", "codes")} if latest else None,
        "reason": None if completed else "no basket ratio for the session: too few ETFs have its bar yet",
        "research_only": True, "live_effect": "none",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="broad-ETF basket flow (decision 0013)")
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    parser.add_argument("--days", type=int, default=FETCH_DAYS, help="calendar days to fetch; 600 for a backfill")
    parser.add_argument("--keep", type=int, default=KEEP_SESSIONS)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)

    async def run() -> dict[str, Any]:
        database = None
        if args.apply:
            from .database import Database
            database = Database()
            database.open()

        async def run_database(function: Callable[..., Any], *values: Any, timeout_seconds: float) -> Any:
            return await asyncio.wait_for(asyncio.to_thread(function, *values), timeout_seconds)

        async def fetch(start: date, end: date) -> dict[str, Any]:
            return await fetch_basket(end - timedelta(days=args.days), end)
        return await refresh(database, args.end, run_database=run_database, fetch=fetch, keep=args.keep, apply=args.apply)
    result = asyncio.run(run())
    print(json.dumps(result, ensure_ascii=False, default=str))
    return 0 if result["status"] == "completed" else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())


__all__ = ["BASE_SESSIONS", "BASKET", "BAR_CAPABILITY", "FLOW_CAPABILITY", "MIN_CODES", "SURGE_RATIO", "fetch_basket",
           "flow_readings", "fuyao_bars", "main", "persist_bars", "refresh", "tencent_bars"]
