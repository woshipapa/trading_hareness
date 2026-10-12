"""Read the bounded opening-auction evidence pool and its scored rows."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from . import limit_up_risk_runtime
from .auction_open_desk import build_auction_pool
from .datasources.storage import enabled_watch_symbols, previous_trading_day
from .settings import integer

CN_TZ = ZoneInfo("Asia/Shanghai")


def _previous_reading(database: Any, session_date: date) -> tuple[date | None, dict[str, Any] | None]:
    previous = previous_trading_day(database, session_date)
    return previous, limit_up_risk_runtime.daily(database, previous) if previous else None


def read_scored_rows(database: Any, session_date: date) -> list[dict[str, Any]]:
    """Return the prior session's risk-reading rows in their stored order."""
    _previous, reading = _previous_reading(database, session_date)
    return [
        {key: row[key] for key in ("symbol", "name", "boards", "risk_rank", "sealed_from_open")}
        for row in (reading or {}).get("rows", [])
    ]


def _broken_board_symbols(database: Any, previous: date | None) -> list[str]:
    if previous is None:
        return []
    start = datetime.combine(previous, time(0), CN_TZ)
    end = start + timedelta(days=1)
    with database.transaction() as connection:
        rows = connection.execute(
            """SELECT symbol FROM quant.market_events
                WHERE event_type=%s AND source=%s AND occurred_at>=%s AND occurred_at<%s
                ORDER BY occurred_at, symbol""",
            ("limit_open_pool", "fuyao_ths", start, end),
        ).fetchall()
    return list(dict.fromkeys(str(row["symbol"]) for row in rows))


def read_pool(database: Any, session_date: date) -> list[str]:
    """Build the prior-risk, broken-board, and enabled-watch pool."""
    previous, reading = _previous_reading(database, session_date)
    if previous is None or reading is None:
        return []
    scored = [str(row["symbol"]) for row in (reading or {}).get("rows", [])]
    broken = _broken_board_symbols(database, previous)
    watches = enabled_watch_symbols(database, limit=100)
    cap = integer("AUCTION_POOL_MAX_SYMBOLS", minimum=1, maximum=300)
    return build_auction_pool(
        scored, broken, watches, cap=cap,
    )


__all__ = ["read_pool", "read_scored_rows"]
