"""Keep the async read pool usable across a tunnel drop, without a restart.

The intraday collectors must be running when the session opens, and every one
of them reads through the async pool.  On 2026-09-17 the db-tunnel dropped its
forwarded connections twice; the sync pool reconnected, the async pool did not,
and the only thing that brought it back was a manual container restart.

The pool cannot escape that state by itself.  ``getconn`` raises
``TooManyRequests`` *before* it considers growing the pool, so once the waiting
queue is full a service with seven retrying collectors keeps it full and the
growth path is never reached again.  This watchdog is what notices and acts.

It is deliberately *not* a leased background task.  A lease is renewed through
the database, so a pool failure would stop the renewal and kill the one loop
whose job is to fix it.  It also runs under every runtime profile: the pool is
not owned by a profile, and the peer's research process needs it as much as the
edge collector does.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from .health_read_model import async_pool_stall_reason

#: Long enough that a healthy pool is barely touched, short enough that a stall
#: cannot survive a lunch break and greet the afternoon session still wedged.
WATCHDOG_INTERVAL_SECONDS = 30.0

#: One observation is not a stall.  A pool momentarily empty while its first
#: connections are being established would otherwise be replaced for no reason,
#: so the signature has to persist across consecutive checks.
WATCHDOG_STALL_CONFIRMATIONS = 3


class AsyncPoolWatchdogState:
    """Process-local evidence of what the watchdog has seen and done."""

    def __init__(self) -> None:
        self.consecutive_stalls = 0
        self.recoveries = 0
        self.last_reason: str | None = None
        self.last_recovered_at: str | None = None

    def snapshot(self) -> dict[str, Any]:
        return {
            "consecutive_stalls": self.consecutive_stalls,
            "recoveries": self.recoveries,
            "last_reason": self.last_reason,
            "last_recovered_at": self.last_recovered_at,
            "confirmations_before_recovery": WATCHDOG_STALL_CONFIRMATIONS,
            "interval_seconds": WATCHDOG_INTERVAL_SECONDS,
        }


async def check_once(
    state: AsyncPoolWatchdogState,
    pool_status: Callable[[], dict[str, Any]],
    replace_pool: Callable[[], Awaitable[None]],
    *,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> str | None:
    """Observe the pool once; replace it only after a confirmed stall.

    Returns the reason a replacement was performed, or None.  The counter is
    reset by any healthy observation, so an intermittent stall never
    accumulates its way to a replacement across unrelated minutes.
    """
    reason = async_pool_stall_reason(pool_status())
    if reason is None:
        state.consecutive_stalls = 0
        return None
    state.consecutive_stalls += 1
    state.last_reason = reason
    if state.consecutive_stalls < WATCHDOG_STALL_CONFIRMATIONS:
        return None
    await replace_pool()
    state.consecutive_stalls = 0
    state.recoveries += 1
    state.last_recovered_at = now().isoformat()
    print(f"async pool watchdog replaced a stalled pool: {reason}")
    return reason


async def watchdog_loop(
    state: AsyncPoolWatchdogState,
    pool_status: Callable[[], dict[str, Any]],
    replace_pool: Callable[[], Awaitable[None]],
    *,
    interval_seconds: float = WATCHDOG_INTERVAL_SECONDS,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> None:
    """Run the observation forever; supervision belongs to the caller."""
    while True:
        await sleep(interval_seconds)
        await check_once(state, pool_status, replace_pool)


__all__ = [
    "WATCHDOG_INTERVAL_SECONDS", "WATCHDOG_STALL_CONFIRMATIONS",
    "AsyncPoolWatchdogState", "check_once", "watchdog_loop",
]
