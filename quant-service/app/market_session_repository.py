"""Exchange-clock plus persisted-calendar gates for provider requests."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Awaitable, Callable

from .market_session_rules import (
    SSE_CALENDAR_SQL, calendar_verdict, exchange_date, observation_clock, realtime_clock, session_verdict,
    weekend_verdict,
)
from .runtime_executors import ExecutorSaturatedError, run_database_blocking
from .error_detail import safe_error_detail

def _calendar_date(now: datetime | None) -> date:
    return exchange_date(now)


def sse_calendar_status(database: Any, calendar_date: date) -> tuple[bool, str]:
    """Return persisted SSE state with a safe diagnostic reason."""
    if (closed := weekend_verdict(calendar_date)) is not None:
        return closed
    with database.transaction() as connection:
        row = connection.execute(SSE_CALENDAR_SQL, (calendar_date,)).fetchone()
    return calendar_verdict(row)


def sse_calendar_open(database: Any, calendar_date: date) -> bool:
    """Return the persisted SSE day state; weekends and gaps fail closed."""
    return sse_calendar_status(database, calendar_date)[0]


async def sse_calendar_open_async(
    database: Any,
    calendar_date: date,
    *,
    database_runner: Callable[..., Awaitable[Any]] = run_database_blocking,
) -> bool:
    """Async-safe SSE gate; local executor pressure conservatively closes it."""
    return (await sse_calendar_status_async(
        database, calendar_date, database_runner=database_runner,
    ))[0]


async def sse_calendar_status_async(
    database: Any,
    calendar_date: date,
    *,
    database_runner: Callable[..., Awaitable[Any]] = run_database_blocking,
) -> tuple[bool, str]:
    """Async-safe SSE state; gaps and local capacity pressure fail closed."""
    if (closed := weekend_verdict(calendar_date)) is not None:
        return closed

    def load_calendar() -> Any:
        with database.transaction() as connection:
            return connection.execute(SSE_CALENDAR_SQL, (calendar_date,)).fetchone()

    try:
        row = await database_runner(load_calendar)
    except ExecutorSaturatedError as error:
        return False, f"local calendar capacity unavailable; fail closed: {safe_error_detail(str(error), 180)}"
    return calendar_verdict(row)


def realtime_market_session(database: Any, api_name: str | None = None,
                            now: datetime | None = None) -> tuple[bool, str]:
    clock = realtime_clock(api_name, now)
    return session_verdict(clock, sse_calendar_status(database, exchange_date(now)) if clock[0] else clock)


async def realtime_market_session_async(database: Any, api_name: str | None = None,
                                        now: datetime | None = None, *,
                                        database_runner: Callable[..., Awaitable[Any]] = run_database_blocking) -> tuple[bool, str]:
    clock = realtime_clock(api_name, now)
    if not clock[0]:
        return clock
    return session_verdict(clock, await sse_calendar_status_async(
        database, exchange_date(now), database_runner=database_runner,
    ))


def market_observation_session(database: Any, now: datetime | None = None) -> tuple[bool, str]:
    """Gate research evidence from 09:15 while retaining the SSE calendar."""
    clock = observation_clock(now)
    return session_verdict(clock, sse_calendar_status(database, exchange_date(now)) if clock[0] else clock)


async def market_observation_session_async(
    database: Any,
    now: datetime | None = None,
    *,
    database_runner: Callable[..., Awaitable[Any]] = run_database_blocking,
) -> tuple[bool, str]:
    """Async-safe 09:15 evidence gate; it never broadens strategy sessions."""
    clock = observation_clock(now)
    if not clock[0]:
        return clock
    return session_verdict(clock, await sse_calendar_status_async(
        database, exchange_date(now), database_runner=database_runner,
    ))


__all__ = [
    "market_observation_session", "market_observation_session_async",
    "realtime_market_session", "realtime_market_session_async",
    "sse_calendar_open", "sse_calendar_open_async", "sse_calendar_status", "sse_calendar_status_async",
]
