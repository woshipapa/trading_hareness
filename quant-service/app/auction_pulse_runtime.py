"""Supervised two-second opening-auction observation loop."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any

from .auction_pulse import auction_pulse_window


async def run_auction_pulse_loop(
    *, interval_seconds: float, capture: Callable[[datetime], Awaitable[dict[str, Any]]],
    session_open: Callable[[datetime], Awaitable[bool]], log: Callable[[str], None] = print,
) -> None:
    """Capture every cadence tick in the 09:15-09:30 Shanghai window.

    The callback owns provider rate limiting, persistence and alert cooldown.
    This runner only schedules bounded observations and never catches a
    cancellation as a normal failure.
    """
    while True:
        now = datetime.now(timezone.utc)
        if auction_pulse_window(now):
            try:
                if await session_open(now):
                    result = await capture(now)
                    if result.get("status") not in {"completed", "partial", "skipped"}:
                        log(f"auction pulse degraded: {str(result)[:300]}")
            except asyncio.CancelledError:
                raise
            except Exception as error:  # noqa: BLE001 - next two-second tick retries
                log(f"auction pulse failed: {type(error).__name__}: {str(error)[:300]}")
        await asyncio.sleep(max(1.0, min(30.0, float(interval_seconds))))


__all__ = ["run_auction_pulse_loop"]
