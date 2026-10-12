"""Compute and persist the research-only daily limit-up risk reading."""

from __future__ import annotations

import argparse
from datetime import date
from typing import Any

from . import derived_daily_readings
from .limit_up_risk import VERSION, score_day
from .limit_up_risk_repository import read_inputs

CAPABILITY = "limit_up_risk_daily"


def _reading(trade_date: date, inputs: dict[str, Any]) -> dict[str, Any]:
    scored = score_day(inputs["lists"], inputs["bars"], inputs["limits"])
    return {
        "trade_date": trade_date.isoformat(), "version": VERSION, "list_source": inputs["list_source"],
        "coverage": scored["coverage"], "ranks_reliable": scored["ranks_reliable"], "rows": scored["rows"],
        "research_only": True, "live_effect": "none",
    }


def refresh(database: Any, trade_date: date, *, apply: bool = True) -> dict[str, Any]:
    with database.transaction() as connection:
        reading = _reading(trade_date, read_inputs(connection, trade_date))
    counts = derived_daily_readings.store(database, CAPABILITY, [reading]) if apply else {"stored": 0, "unchanged": 0}
    return {"status": "completed", "trade_date": trade_date.isoformat(), **counts,
            "rows": len(reading["rows"]), "research_only": True, "live_effect": "none"}


def daily(database: Any, day: date) -> dict[str, Any] | None:
    with database.transaction() as connection:
        readings = derived_daily_readings.newest(connection, CAPABILITY, day, day)
    return readings[0] if readings else None


def track(database: Any, days: int = 250) -> dict[str, Any]:
    with database.transaction() as connection:
        readings = derived_daily_readings.newest(connection, CAPABILITY, date.min, date.max)
        summaries = []
        for reading in readings[-days:]:
            rows = reading.get("rows") or []
            if len(rows) < 20:
                continue
            trade_date = date.fromisoformat(reading["trade_date"])
            next_day = connection.execute(
                """SELECT min(calendar_date) AS day FROM quant.market_trade_calendar
                     WHERE exchange='SSE' AND is_open AND calendar_date>%s""", (trade_date,),
            ).fetchone()["day"]
            if next_day is None:
                continue
            symbols = [row["symbol"] for row in rows]
            bars = connection.execute(
                """SELECT symbol,trading_date,close FROM quant.canonical_bars_daily
                     WHERE symbol=ANY(%s) AND trading_date IN (%s,%s)""", (symbols, trade_date, next_day),
            ).fetchall()
            closes = {(str(row["symbol"]), row["trading_date"]): float(row["close"]) for row in bars}
            outcomes = [
                (row, closes[(row["symbol"], next_day)] / closes[(row["symbol"], trade_date)] - 1)
                for row in rows if (row["symbol"], trade_date) in closes and (row["symbol"], next_day) in closes
            ]
            outcomes.sort(key=lambda item: item[0]["risk"])
            fifth = len(outcomes) // 5
            if fifth < 1:
                continue
            groups = (outcomes[:fifth], outcomes[-fifth:])
            summaries.append({
                "trade_date": reading["trade_date"], "lowest": _fifth(groups[0]), "highest": _fifth(groups[1]),
            })
    return {"version": VERSION, "days": summaries, "research_only": True, "live_effect": "none"}


def _fifth(outcomes: list[tuple[dict[str, Any], float]]) -> dict[str, float]:
    returns = [item[1] for item in outcomes]
    return {"big_loss_rate": sum(value <= -0.05 for value in returns) / len(returns),
            "mean_next_day_return": sum(returns) / len(returns)}


def _calendar_days(database: Any, start: date, end: date) -> list[date]:
    with database.transaction() as connection:
        rows = connection.execute(
            """SELECT calendar_date FROM quant.market_trade_calendar
                 WHERE exchange='SSE' AND is_open AND calendar_date BETWEEN %s AND %s ORDER BY calendar_date""",
            (start, end),
        ).fetchall()
    return [row["calendar_date"] for row in rows]


def main() -> None:  # pragma: no cover
    from . import main as service

    parser = argparse.ArgumentParser()
    parser.add_argument("--start", required=True, type=date.fromisoformat)
    parser.add_argument("--end", required=True, type=date.fromisoformat)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    for day in _calendar_days(service.db, args.start, args.end):
        print(refresh(service.db, day, apply=args.apply))


if __name__ == "__main__":  # pragma: no cover
    main()


__all__ = ["CAPABILITY", "daily", "refresh", "track"]
