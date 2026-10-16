"""Runtime loop for bounded Longhu-first order-book observation.

Provider decoding/persistence belongs to ``intraday_order_book_service``.  This
module only coordinates session gates, local storage policy and daily pruning,
so it can be exercised without a FastAPI app or external requests.
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone
from typing import Any, Awaitable, Callable
from zoneinfo import ZoneInfo


AsyncCall = Callable[..., Awaitable[Any]]


async def run_iteration(
    pruned_on: date | None,
    *,
    realtime_session: AsyncCall,
    open_capabilities: AsyncCall,
    load_symbols: AsyncCall,
    prune_before: AsyncCall,
    storage_allowed: AsyncCall,
    capture: AsyncCall,
    interval_seconds: Callable[[], float],
    retention_days: Callable[[], int],
    now_utc: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    emit: Callable[[str], None] = print,
) -> tuple[date | None, float]:
    """Run at most one capture cycle and return state plus the next sleep."""
    interval = interval_seconds()
    active, _ = await realtime_session()
    if not active:
        return pruned_on, interval
    # These are independent local circuit reads.  Run them together after the
    # session gate so an inactive market does not create needless DB work.
    longhu_task = asyncio.create_task(
        open_capabilities("longhuvip", ["order_book_quote"]), name="order-book-longhu-circuit",
    )
    tencent_task = asyncio.create_task(
        open_capabilities("tencent_free", ["order_book_quote"]), name="order-book-tencent-circuit",
    )
    try:
        longhu_open, tencent_open = await asyncio.gather(longhu_task, tencent_task)
    except BaseException:
        for task in (longhu_task, tencent_task):
            if not task.done():
                task.cancel()
        await asyncio.gather(longhu_task, tencent_task, return_exceptions=True)
        raise
    longhu_circuit_open = "order_book_quote" in longhu_open
    tencent_circuit_open = "order_book_quote" in tencent_open
    if longhu_circuit_open and tencent_circuit_open:
        # Only skip when both the Longhu primary and Tencent fallback are
        # unavailable.  A Tencent circuit must never suppress a healthy
        # Longhu capture (and vice versa).
        return pruned_on, max(15.0, interval)

    symbols = await load_symbols()
    local_date = now_utc().astimezone(ZoneInfo("Asia/Shanghai")).date()
    if pruned_on != local_date:
        await prune_before(now_utc(), retention_days())
        pruned_on = local_date
    allowed, storage = await storage_allowed()
    if not allowed:
        emit(f"intraday order-book capture skipped by storage guard: {storage.get('state')}")
        return pruned_on, interval
    result = await capture(symbols)
    if result.get("status") == "failed":
        emit(f"intraday order-book capture failed: {str(result.get('reason') or '')[:300]}")
    return pruned_on, interval


async def run_loop(**dependencies: Any) -> None:
    """Run observation forever; exceptions remain visible to the supervisor."""
    pruned_on: date | None = None
    while True:
        pruned_on, sleep_seconds = await run_iteration(pruned_on, **dependencies)
        await asyncio.sleep(sleep_seconds)


__all__ = ["run_iteration", "run_loop"]
