"""A small in-process cache for heavy, slowly changing reads.

On 2026-10-09 two console reads took 7 s (analyst skills) and about 15 s
(factor evaluations) on the owner. A console opening fires them alongside
50 others, and they held database connections while the minute capture's
writes queued everything behind them. Their data changes after the close,
not by the minute. So one computation serves every caller for a short time,
and concurrent callers share the computation in flight instead of each
starting their own.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Hashable
from typing import Any, TypeVar

T = TypeVar("T")


class TTLCache:
    def __init__(self, ttl_seconds: float, *, max_entries: int = 64, clock: Callable[[], float] = time.monotonic) -> None:
        self.ttl_seconds = ttl_seconds
        self.max_entries = max_entries
        self.clock = clock
        self._entries: dict[Hashable, tuple[float, Any]] = {}
        self._inflight: dict[Hashable, asyncio.Future[Any]] = {}

    async def get(self, key: Hashable, compute: Callable[[], Awaitable[T]]) -> T:
        entry = self._entries.get(key)
        if entry is not None and self.clock() - entry[0] < self.ttl_seconds:
            return entry[1]
        pending = self._inflight.get(key)
        if pending is not None:
            return await asyncio.shield(pending)
        future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        self._inflight[key] = future
        try:
            value = await compute()
        except BaseException as error:
            future.set_exception(error)
            future.exception()      # retrieved here, so an unawaited future does not warn
            raise
        else:
            future.set_result(value)
            if len(self._entries) >= self.max_entries:
                self._entries.pop(next(iter(self._entries)))
            self._entries[key] = (self.clock(), value)
            return value
        finally:
            self._inflight.pop(key, None)


__all__ = ["TTLCache"]
