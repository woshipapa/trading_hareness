"""The exchange-session decision, shared by the sync and the native-async gates.

A session is open only when the exchange clock says so *and* the persisted SSE
calendar marks the day open; weekends, a missing row or a closed row fail
closed. This was written out three times - sync, executor-async and native
async - with the same SQL and reason strings; the repositories now differ only
in how they fetch the row.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from .market_rules import china_equity_observation_session, china_equity_session, china_futures_session

CN_TZ = ZoneInfo("Asia/Shanghai")
SSE_CALENDAR_SQL = "SELECT is_open FROM quant.market_trade_calendar WHERE exchange='SSE' AND calendar_date=%s"


def exchange_date(now: datetime | None) -> date:
    return (now or datetime.now(timezone.utc)).astimezone(CN_TZ).date()


def weekend_verdict(calendar_date: date) -> tuple[bool, str] | None:
    """Weekends are closed without asking the database."""
    return (False, "SSE trade calendar treats weekends as closed") if calendar_date.weekday() >= 5 else None


def calendar_verdict(row: Mapping[str, Any] | None) -> tuple[bool, str]:
    if row is None:
        return False, "SSE trade calendar has no entry for today; fail closed"
    if not row["is_open"]:
        return False, "SSE trade calendar marks today closed"
    return True, "SSE trade calendar marks today open"


def realtime_clock(api_name: str | None, now: datetime | None) -> tuple[bool, str]:
    return china_futures_session(now) if api_name == "rt_fut_min" else china_equity_session(now)


def observation_clock(now: datetime | None) -> tuple[bool, str]:
    """Evidence collectors open at 09:15 without widening strategy hours."""
    return china_equity_observation_session(now)


def session_verdict(clock: tuple[bool, str], calendar: tuple[bool, str]) -> tuple[bool, str]:
    """Open only when both say open; the reason names whichever closed it."""
    active, reason = clock
    if not active:
        return active, reason
    calendar_open, calendar_reason = calendar
    return (True, reason) if calendar_open else (False, calendar_reason)


__all__ = [
    "CN_TZ", "SSE_CALENDAR_SQL", "calendar_verdict", "exchange_date", "observation_clock",
    "realtime_clock", "session_verdict", "weekend_verdict",
]
