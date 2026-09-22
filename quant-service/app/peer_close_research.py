"""The peer's own post-close research stages, on the peer's own schedule.

The full post-close refresh (``post_close_refresh_service``) is the owner's:
its 15681 runs its own pipeline at every close, while the peer's copy is only
reachable by hand.  These stages belong to the peer alone - the teacher plan
roll, its outcome review, the watch-list daily review and the 小杰 settlement -
so they never ran on their own: after 2026-09-22's close the 09-21 teacher
pack's plans were neither settled nor carried into 09-23.

This runs just those, once per trading date, after the day's bars have
landed, each behind the same durable receipt the orchestrator would use (a
completed stage is never repeated).  Before 09:00 it catches up the previous
session, so a restart overnight does not skip a day; between 09:00 and the
close-ready time it does nothing.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from datetime import date, datetime, time, timedelta
from typing import Any

#: Order matters: the outcome review reads the settlement the roll just wrote.
STAGES = ("teacher_review_roll", "teacher_outcome_review", "watch_daily_review", "xiaojie_outcomes")
STAGE_TIMEOUT_SECONDS = {"teacher_review_roll": 300.0, "teacher_outcome_review": 240.0,
                         "watch_daily_review": 240.0, "xiaojie_outcomes": 150.0}
#: The owner's close pipeline has published the day's bars by ~16:05.
CLOSE_READY_AT = time(16, 15)
SESSION_GUARD_FROM = time(9, 0)
#: A session's full-market bars count as landed above this many symbols.
MIN_DAILY_BARS = 4000


async def target_session(now_local: datetime, calendar_open: Callable[[date], Awaitable[bool]]) -> date | None:
    """The session whose close research is due now, if any."""
    today = now_local.date()
    if now_local.time() >= CLOSE_READY_AT:
        return today if await calendar_open(today) else None
    if now_local.time() >= SESSION_GUARD_FROM:
        return None
    for back in range(1, 12):
        day = today - timedelta(days=back)
        if await calendar_open(day):
            return day
    return None


def daily_bars_ready(database: Any, trade_date: date) -> bool:
    with database.transaction() as connection:
        row = connection.execute(
            "SELECT count(*)::int AS n FROM quant.canonical_bars_daily WHERE trading_date=%s", (trade_date,),
        ).fetchone()
    return int((row or {}).get("n") or 0) >= MIN_DAILY_BARS


async def run_due(trade_date: date, *, stages: Mapping[str, Callable[[date], Awaitable[Any]]],
                  record: Callable[[str, date, Callable[[], Any]], Awaitable[Any]]) -> dict[str, Any]:
    """Run each stage behind its receipt; one failure never stops the next."""
    results: dict[str, Any] = {}
    for name in STAGES:
        action = stages.get(name)
        if action is None:
            continue

        async def bounded(name: str = name, action: Callable[[date], Awaitable[Any]] = action) -> Any:
            return await asyncio.wait_for(action(trade_date), timeout=STAGE_TIMEOUT_SECONDS.get(name, 120.0))

        try:
            result = await record(name, trade_date, bounded)
            results[name] = (result or {}).get("status", "completed") if isinstance(result, dict) else "completed"
        except Exception as error:  # noqa: BLE001 - the receipt records the failure; later stages still run
            results[name] = f"failed: {str(error)[:160]}"
    return {"trade_date": trade_date.isoformat(), "stages": results}


__all__ = ["CLOSE_READY_AT", "MIN_DAILY_BARS", "STAGES", "daily_bars_ready", "run_due", "target_session"]
