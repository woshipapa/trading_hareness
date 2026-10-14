"""Compute and persist the research-only daily limit-up risk reading."""

from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta
from typing import Any

from . import derived_daily_readings
from .derived_daily_readings import CN_TZ
from .limit_up_risk import VERSION, fifth_comparison, open_to_close_status, recent_status, score_day
from .limit_up_risk_repository import read_inputs

CAPABILITY = "limit_up_risk_daily"


def _reading(trade_date: date, inputs: dict[str, Any]) -> dict[str, Any]:
    scored = score_day(inputs["lists"], inputs["bars"])
    return {
        "trade_date": trade_date.isoformat(), "version": VERSION, "list_source": inputs["list_source"],
        "coverage": scored["coverage"], "ranks_reliable": scored["ranks_reliable"], "rows": scored["rows"],
        "research_only": True, "live_effect": "none",
    }


def refresh(database: Any, trade_date: date, *, apply: bool = True) -> dict[str, Any]:
    with database.transaction() as connection:
        inputs = read_inputs(connection, trade_date)
        if not any(inputs["lists"].values()):
            return {"status": "no_lists", "trade_date": trade_date.isoformat(), "stored": 0}
        reading = _reading(trade_date, inputs)
    counts = derived_daily_readings.store(database, CAPABILITY, [reading]) if apply else {"stored": 0, "unchanged": 0}
    return {"status": "completed", "trade_date": trade_date.isoformat(), **counts,
            "rows": len(reading["rows"]), "research_only": True, "live_effect": "none"}


def daily(database: Any, day: date | None = None) -> dict[str, Any] | None:
    """The reading of ``day`` (the newest one when no day is given), or None."""
    with database.transaction() as connection:
        return daily_series(connection, day)["reading"]


def daily_series(connection: Any, day: date | None = None) -> dict[str, Any]:
    end = day or datetime.now(CN_TZ).date()
    # A requested day reads that day only; the newest reading is looked for over two weeks, longer than any holiday.
    stored = derived_daily_readings.newest(connection, CAPABILITY, day or end - timedelta(days=14), end)
    valid = [reading for reading in stored if reading.get("version") == VERSION]
    return {"version": VERSION, "reading": valid[-1] if valid else None,
            "stale_versions": len(stored) - len(valid), "research_only": True, "live_effect": "none"}


def track(database: Any, days: int = 250) -> dict[str, Any]:
    with database.transaction() as connection:
        sessions = connection.execute(
            """SELECT calendar_date,
                      (SELECT min(next_day.calendar_date) FROM quant.market_trade_calendar next_day
                        WHERE next_day.exchange='SSE' AND next_day.is_open
                          AND next_day.calendar_date>calendar.calendar_date) AS next_day
                 FROM quant.market_trade_calendar calendar
                WHERE exchange='SSE' AND is_open AND calendar_date <= %s
                ORDER BY calendar_date DESC LIMIT %s""", (datetime.now(CN_TZ).date(), days),
        ).fetchall()
        sessions = list(reversed(sessions))
        if not sessions:
            return {"version": VERSION, "days": [], "recent": recent_status([]), "stale_versions": 0,
                    "research_only": True, "live_effect": "none"}
        readings = derived_daily_readings.newest(
            connection, CAPABILITY, sessions[0]["calendar_date"], sessions[-1]["calendar_date"],
        )
        stale_versions = len(readings) - sum(reading.get("version") == VERSION for reading in readings)
        readings = [reading for reading in readings if reading.get("version") == VERSION]
        next_days = {row["calendar_date"]: row["next_day"] for row in sessions}
        needed = []
        for reading in readings:
            day = date.fromisoformat(reading["trade_date"])
            next_day = next_days.get(day)
            needed.extend((row["symbol"], value) for row in (reading.get("rows") or []) for value in (day, next_day) if next_day)
        symbols = [symbol for symbol, _day in needed]
        dates = [day for _symbol, day in needed]
        bars = connection.execute(
            """SELECT b.symbol,b.trading_date,b.open,b.close FROM quant.canonical_bars_daily b
                JOIN unnest(%s::text[], %s::date[]) AS needed(symbol,trading_date)
                   ON needed.symbol=b.symbol AND needed.trading_date=b.trading_date
                WHERE NOT coalesce(b.is_suspended, false) AND b.pre_close > 0 AND b.open > 0 AND b.close > 0""",
            (symbols, dates),
        ).fetchall() if needed else []
        closes = {(str(row["symbol"]), row["trading_date"]): float(row["close"]) for row in bars}
        opens = {(str(row["symbol"]), row["trading_date"]): float(row["open"]) for row in bars}
        summaries = []
        open_to_close_summaries = []
        for reading in readings:
            rows = reading.get("rows") or []
            day = date.fromisoformat(reading["trade_date"])
            next_day = next_days.get(day)
            if next_day is None:
                continue
            returns = {row["symbol"]: closes[(row["symbol"], next_day)] / closes[(row["symbol"], day)] - 1
                       for row in rows if (row["symbol"], day) in closes and (row["symbol"], next_day) in closes}
            # What a buyer at the next open meets: the overnight gap is not his.
            intraday_returns = {row["symbol"]: closes[(row["symbol"], next_day)] / opens[(row["symbol"], next_day)] - 1
                                for row in rows if (row["symbol"], next_day) in closes}
            comparison = fifth_comparison(rows, returns)
            intraday = fifth_comparison(rows, intraday_returns)
            if intraday is not None:
                open_to_close_summaries.append({"trade_date": reading["trade_date"], **intraday})
            if comparison is not None:
                summaries.append({"trade_date": reading["trade_date"], **comparison,
                                  **({"open_to_close": intraday} if intraday is not None else {})})
    return {"version": VERSION, "days": summaries, "recent": recent_status(summaries),
            # ``recent`` above is close to close and is kept as it was; the judgement that counts for a buyer is
            # this one: the high-risk fifth must do worse from the open to the close, after costs.
            "recent_open_to_close": open_to_close_status(open_to_close_summaries),
            "judged_on": "next_session_open_to_close", "stale_versions": stale_versions,
            "research_only": True, "live_effect": "none"}


def _calendar_days(database: Any, start: date, end: date) -> list[date]:
    with database.transaction() as connection:
        rows = connection.execute(
            """SELECT calendar_date FROM quant.market_trade_calendar
                 WHERE exchange='SSE' AND is_open AND calendar_date BETWEEN %s AND %s ORDER BY calendar_date""",
            (start, end),
        ).fetchall()
    return [row["calendar_date"] for row in rows]


def main() -> None:  # pragma: no cover
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", required=True, type=date.fromisoformat)
    parser.add_argument("--end", required=True, type=date.fromisoformat)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    from .database import Database
    database = Database()
    database.open()
    for day in _calendar_days(database, args.start, args.end):
        print(refresh(database, day, apply=args.apply))


if __name__ == "__main__":  # pragma: no cover
    main()


__all__ = ["CAPABILITY", "daily", "daily_series", "refresh", "track"]
