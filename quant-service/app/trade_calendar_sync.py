"""Forward exchange calendar from Fuyao, accepted only where it agrees with what we hold.

Future ``quant.market_trade_calendar`` rows came only from Tushare ``trade_cal``.
Tushare was retired on 2026-10-08, and the intraday session gate fails closed
on any date without a row (``async_market_session_repository``), so the day the
pre-fetched rows run out every intraday loop stops. Longhu records only sessions
it has already seen settle.

Fuyao's ``a_share_trading_days`` lists exchange sessions. Its answer is used
only if, across its whole span, it agrees exactly with every date already held -
open and closed alike - and it then only adds rows for dates that have none,
never rewriting one. An answer that cannot be read, is too thin, or disagrees
with the held calendar writes nothing and says why.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta
import re
from typing import Any

PROVIDER_KEY = "fuyao_ths"
EXCHANGES = ("SSE", "SZSE", "BSE")  # A-share exchanges share one session calendar
MIN_SESSIONS = 150                  # a thinner list is not a calendar
#: Held future sessions that let a vendor list ending today pass: about a month of
#: warning before the held calendar runs out and the intraday session gate closes.
RUNWAY_SESSIONS = 20
_DATE = re.compile(r"^(\d{4})-?(\d{2})-?(\d{2})")
_DATE_FIELDS = ("trade_date", "trading_day", "calendar_date", "date", "cal_date", "day")


def _as_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    match = _DATE.match(str(value or "").strip())
    if not match:
        return None
    try:
        return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    except ValueError:
        return None


def _dates_from(items: Iterable[Any]) -> list[date] | None:
    found: list[date] = []
    for item in items:
        if isinstance(item, Mapping):
            if str(item.get("is_open", "1")).strip().lower() in {"0", "false", "no"}:
                continue
            value = next((item[field] for field in _DATE_FIELDS if field in item), None)
        else:
            value = item
        parsed = _as_date(value)
        if parsed is None:
            return None
        found.append(parsed)
    return found


def extract_trading_days(data: Any) -> list[date]:
    """The first list of dates in the response, wherever the vendor put it; [] if none."""
    queue: list[Any] = [data]
    while queue:
        node = queue.pop(0)
        if isinstance(node, list) and node:
            dates = _dates_from(node)
            if dates:
                return sorted(set(dates))
        if isinstance(node, Mapping):
            queue.extend(node.values())
    return []


@dataclass(frozen=True)
class CalendarPlan:
    status: str                      # ready, blocked
    reason: str | None
    rows: tuple[tuple[date, bool, date | None], ...] = ()
    span: tuple[date, date] | None = None
    agreed_dates: int = 0


def plan_forward_rows(trading_days: list[date], held: Mapping[date, bool], *, today: date) -> CalendarPlan:
    """Rows for the dates after the last held one, if the vendor agrees with every held date it covers."""
    sessions = sorted(set(trading_days))
    if len(sessions) < MIN_SESSIONS:
        return CalendarPlan("blocked", f"only {len(sessions)} sessions; a calendar needs at least {MIN_SESSIONS}")
    weekend = [day for day in sessions if day.weekday() >= 5]
    if weekend:
        return CalendarPlan("blocked", f"weekend dates listed as sessions, e.g. {weekend[0]}")
    first, last = sessions[0], sessions[-1]
    if last <= today:
        return CalendarPlan("blocked", f"the list ends at {last}, not after today ({today})", span=(first, last))
    open_days = set(sessions)
    disagreements = [day for day, is_open in held.items() if first <= day <= last and (day in open_days) != bool(is_open)]
    if disagreements:
        sample = ", ".join(f"{day} held {'open' if held[day] else 'closed'}" for day in sorted(disagreements)[:3])
        return CalendarPlan("blocked", f"{len(disagreements)} date(s) disagree with the held calendar: {sample}",
                            span=(first, last))
    agreed = sum(1 for day in held if first <= day <= last)
    if not agreed:
        return CalendarPlan("blocked", "no overlap with the held calendar to check the list against", span=(first, last))
    last_held = max(held) if held else today
    rows: list[tuple[date, bool, date | None]] = []
    previous_open = max((day for day, is_open in held.items() if is_open), default=None)
    day = last_held + timedelta(days=1)
    while day <= last:
        is_open = day in open_days
        rows.append((day, is_open, previous_open))
        if is_open:
            previous_open = day
        day += timedelta(days=1)
    return CalendarPlan("ready", None, tuple(rows), (first, last), agreed)


def read_held_calendar(connection: Any, exchange: str = "SSE") -> dict[date, bool]:
    rows = connection.execute(
        "SELECT calendar_date,is_open FROM quant.market_trade_calendar WHERE exchange=%s", (exchange,),
    ).fetchall()
    return {row["calendar_date"]: bool(row["is_open"]) for row in rows}


def persist_forward_rows(connection: Any, rows: Iterable[tuple[date, bool, date | None]],
                         observed_at: datetime, request_id: str | None) -> int:
    """Insert rows for dates that have none; a held date is never rewritten."""
    from psycopg.types.json import Json

    stored = 0
    for exchange in EXCHANGES:
        for calendar_date, is_open, pretrade_date in rows:
            result = connection.execute(
                """INSERT INTO quant.market_trade_calendar(
                       exchange,calendar_date,is_open,pretrade_date,provider,available_at,raw)
                   VALUES(%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT(exchange,calendar_date) DO NOTHING""",
                (exchange, calendar_date, is_open, pretrade_date, PROVIDER_KEY, observed_at,
                 Json({"derivation": "fuyao_a_share_trading_days_checked_against_held_calendar",
                       "request_id": request_id})),
            )
            stored += int(getattr(result, "rowcount", 0) or 0)
    return stored


async def sync_forward_calendar(
    *,
    fetch_envelope: Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]],
    read_held: Callable[[], Awaitable[dict[date, bool]]],
    persist: Callable[[tuple[tuple[date, bool, date | None], ...], str | None], Awaitable[int]],
    today: date,
) -> dict[str, Any]:
    envelope = await fetch_envelope("a_share_trading_days", {})
    sessions = extract_trading_days(envelope.get("data"))
    if not sessions:
        return {"status": "blocked", "provider": PROVIDER_KEY, "reason": "no list of dates in the response",
                "request_id": envelope.get("request_id"), "data_keys": sorted((envelope.get("data") or {}).keys())[:10]}
    held = await read_held()
    plan = plan_forward_rows(sessions, held, today=today)
    result = {
        "status": plan.status, "provider": PROVIDER_KEY, "reason": plan.reason,
        "request_id": envelope.get("request_id"), "sessions": len(sessions),
        "span": [str(plan.span[0]), str(plan.span[1])] if plan.span else None,
        "agreed_held_dates": plan.agreed_dates,
        "held_through": str(max(held)) if held else None,
    }
    if plan.status != "ready":
        # A list that stops at today adds nothing but is no emergency while the held
        # calendar still runs well ahead (2026-10-09: Fuyao ended at the day itself
        # while the held SSE calendar ran to 12-31). Fewer held sessions than the
        # runway stays blocked, a month before the intraday gate would close.
        runway = sorted(day for day, is_open in held.items() if is_open and day > today)
        if plan.span and plan.span[1] <= today and len(runway) >= RUNWAY_SESSIONS:
            return {**result, "status": "completed", "new_dates": 0, "runway_sessions": len(runway),
                    "calendar_through": str(max(held)),
                    "note": f"vendor list ends at {plan.span[1]}; held calendar covers {len(runway)} sessions "
                            f"through {runway[-1]}"}
        if plan.span and plan.span[1] <= today:
            result["runway_sessions"] = len(runway)
            result["reason"] = (f"{plan.reason}; only {len(runway)} held future sessions remain "
                                f"(need {RUNWAY_SESSIONS}) - add next year's calendar")
        return result
    stored = await persist(plan.rows, envelope.get("request_id")) if plan.rows else 0
    return {**result, "status": "completed", "new_dates": len(plan.rows), "stored_rows": stored,
            "calendar_through": str(plan.rows[-1][0]) if plan.rows else result["held_through"]}


__all__ = [
    "CalendarPlan", "RUNWAY_SESSIONS", "extract_trading_days", "persist_forward_rows", "plan_forward_rows",
    "read_held_calendar", "sync_forward_calendar",
]
