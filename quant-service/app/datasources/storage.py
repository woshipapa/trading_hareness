"""Small read projections used by the public evidence collectors.

Reads only; the collectors write through ``public_market_repository``.
"""

from __future__ import annotations

from datetime import date, datetime, time
from typing import Any
from zoneinfo import ZoneInfo


CN_TZ = ZoneInfo("Asia/Shanghai")
FUYAO_CONCEPT_TAXONOMY = "fuyao_ths_concept"


def enabled_watch_symbols(database: Any, limit: int = 100) -> list[str]:
    with database.transaction() as connection:
        rows = connection.execute(
            "SELECT symbol FROM quant.intraday_watchlists WHERE enabled ORDER BY updated_at DESC, symbol LIMIT %s",
            (max(1, min(limit, 300)),),
        ).fetchall()
    return [str(row["symbol"]) for row in rows]


def previous_trading_day(database: Any, day: date) -> date | None:
    with database.transaction() as connection:
        row = connection.execute(
            """SELECT max(calendar_date) AS previous FROM quant.market_trade_calendar
                WHERE exchange='SSE' AND is_open AND calendar_date < %s""",
            (day,),
        ).fetchone()
    return row["previous"] if row and row["previous"] else None


def previous_close_turnover(database: Any, day: date) -> float | None:
    """The previous session's all-A turnover from its archived close reading."""
    start_of_day = datetime.combine(day, time(0, 0), CN_TZ)
    with database.transaction() as connection:
        row = connection.execute(
            """SELECT payload->'turnover'->>'total_yuan' AS total
                 FROM quant.raw_market_observations
                WHERE capability='market_sentiment_close' AND symbol IS NULL
                  AND provider_key='derived_market_sentiment' AND effective_at < %s
                ORDER BY effective_at DESC LIMIT 1""",
            (start_of_day,),
        ).fetchone()
    try:
        return float(row["total"]) if row and row["total"] is not None else None
    except (TypeError, ValueError):
        return None


def fuyao_concept_membership(database: Any) -> dict[str, set[str]] | None:
    """Current THS concept membership loaded by the Fuyao fill script, if any."""
    with database.transaction() as connection:
        rows = connection.execute(
            """SELECT sector_key, symbol FROM quant.sector_membership_history
                WHERE taxonomy_key=%s AND effective_to IS NULL""",
            (FUYAO_CONCEPT_TAXONOMY,),
        ).fetchall()
    if not rows:
        return None
    membership: dict[str, set[str]] = {}
    for row in rows:
        membership.setdefault(str(row["sector_key"]), set()).add(str(row["symbol"]))
    return membership


__all__ = [
    "FUYAO_CONCEPT_TAXONOMY", "enabled_watch_symbols", "fuyao_concept_membership",
    "previous_close_turnover", "previous_trading_day",
]
