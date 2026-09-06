"""Cadence-only runner for all-A auction/limit-pool evidence capture."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from datetime import datetime, time, timezone
from typing import Any
from zoneinfo import ZoneInfo

CN_TZ = ZoneInfo("Asia/Shanghai")


def event_capture_window(now: datetime) -> tuple[bool, bool]:
    local = now.astimezone(CN_TZ)
    if local.weekday() >= 5:
        return False, False
    current = local.time()
    active = (time(9, 20) <= current <= time(11, 30)) or (time(13, 0) <= current <= time(15, 0))
    return active, current >= time(14, 57)


async def run_market_event_capture_loop(
    *,
    interval_seconds: int,
    capture: Callable[..., Awaitable[dict[str, Any]]],
    capture_longhu_auction: Callable[[datetime], Awaitable[dict[str, Any]]] | None = None,
    session_open: Callable[[datetime], Awaitable[bool]],
    symbols: Callable[[], Awaitable[Sequence[str]]],
    log: Callable[[str], None] = print,
) -> None:
    last_auction_date: str | None = None
    last_longhu_auction_date: str | None = None
    while True:
        now = datetime.now(timezone.utc)
        active, auction_window = event_capture_window(now)
        if active and await session_open(now):
            local_date = now.astimezone(CN_TZ).date().isoformat()
            include_auction = auction_window and last_auction_date != local_date
            try:
                result = await capture(
                    now, include_auction=include_auction,
                    auction_symbols=await symbols() if include_auction else (),
                )
                if include_auction and result.get("auction", {}).get("received", 0) > 0:
                    last_auction_date = local_date
                if capture_longhu_auction and time(9, 25) <= now.astimezone(CN_TZ).time() <= time(9, 30) and last_longhu_auction_date != local_date:
                    longhu_result = await capture_longhu_auction(now)
                    if longhu_result.get("status") == "completed":
                        last_longhu_auction_date = local_date
                    elif longhu_result.get("status") not in {"completed", "skipped"}:
                        log(f"Longhu morning-auction evidence degraded: {str(longhu_result)[:500]}")
                if result.get("status") not in {"completed", "empty"}:
                    log(f"market event evidence capture degraded: {str(result)[:500]}")
            except Exception as error:  # noqa: BLE001 - next cadence retries
                log(f"market event evidence capture failed: {str(error)[:300]}")
        await asyncio.sleep(max(10, min(300, int(interval_seconds))))


__all__ = ["event_capture_window", "run_market_event_capture_loop"]
