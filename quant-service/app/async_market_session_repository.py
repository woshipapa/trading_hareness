"""Native-async exchange-calendar gates for production runtime loops.

The legacy repository keeps its executor-injected compatibility interface.
This module is used by the live composition root so frequent calendar checks
do not consume a blocking-executor slot.  Any local pool failure fails closed.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from .market_session_rules import (
    SSE_CALENDAR_SQL, calendar_verdict, exchange_date, observation_clock, realtime_clock, session_verdict,
    weekend_verdict,
)
from .error_detail import safe_error_detail


async def sse_calendar_status(async_database: Any, calendar_date: date) -> tuple[bool, str]:
    if (closed := weekend_verdict(calendar_date)) is not None:
        return closed
    try:
        async with async_database.transaction() as connection:
            result = await connection.execute(SSE_CALENDAR_SQL, (calendar_date,))
            row = await result.fetchone()
    except Exception as error:  # noqa: BLE001 - a calendar failure must stop provider traffic
        return False, f"local calendar unavailable; fail closed: {safe_error_detail(str(error), 180)}"
    return calendar_verdict(row)


async def sse_calendar_open(async_database: Any, calendar_date: date) -> bool:
    return (await sse_calendar_status(async_database, calendar_date))[0]


async def realtime_market_session(
    async_database: Any,
    api_name: str | None = None,
    now: datetime | None = None,
) -> tuple[bool, str]:
    clock = realtime_clock(api_name, now)
    if not clock[0]:
        return clock
    return session_verdict(clock, await sse_calendar_status(async_database, exchange_date(now)))


async def market_observation_session(
    async_database: Any,
    now: datetime | None = None,
) -> tuple[bool, str]:
    """Open evidence collectors at 09:15 without widening strategy hours."""
    clock = observation_clock(now)
    if not clock[0]:
        return clock
    return session_verdict(clock, await sse_calendar_status(async_database, exchange_date(now)))


__all__ = ["market_observation_session", "realtime_market_session", "sse_calendar_open", "sse_calendar_status"]
