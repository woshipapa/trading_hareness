"""Small keyed async memo used to coalesce one bounded workflow stage.

The memo is intentionally narrow: it shares only successful results for a
short, explicit TTL and keeps failed calls retryable.  In-flight callers join
the same task, so two stages that ask for the same dated evidence cannot send
two provider requests at once.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Generic, TypeVar


KeyT = TypeVar("KeyT")
ValueT = TypeVar("ValueT")


class AsyncKeyedMemo(Generic[KeyT, ValueT]):
    """Coalesce keyed calls and retain cacheable results for a bounded TTL."""

    def __init__(
        self,
        fetch: Callable[[KeyT], Awaitable[ValueT]],
        *,
        ttl_seconds: float,
        clock: Callable[[], float] | None = None,
        cacheable: Callable[[ValueT], bool] | None = None,
        max_entries: int = 8,
    ) -> None:
        self._fetch = fetch
        self._ttl_seconds = max(0.0, float(ttl_seconds))
        self._clock = clock or (lambda: asyncio.get_running_loop().time())
        self._cacheable = cacheable or (lambda _value: True)
        self._max_entries = max(1, int(max_entries))
        self._cached: dict[KeyT, tuple[float, ValueT]] = {}
        self._inflight: dict[KeyT, asyncio.Task[ValueT]] = {}

    async def get(self, key: KeyT) -> ValueT:
        now = self._clock()
        cached = self._cached.get(key)
        if cached is not None and now - cached[0] <= self._ttl_seconds:
            return cached[1]
        if cached is not None:
            self._cached.pop(key, None)

        task = self._inflight.get(key)
        if task is None or task.done():
            task = asyncio.create_task(self._fetch(key), name=f"keyed-memo:{key}")
            self._inflight[key] = task
        try:
            value = await asyncio.shield(task)
        except BaseException:
            if self._inflight.get(key) is task:
                self._inflight.pop(key, None)
            raise
        finally:
            if self._inflight.get(key) is task and task.done():
                self._inflight.pop(key, None)

        if self._cacheable(value):
            self._cached[key] = (self._clock(), value)
            while len(self._cached) > self._max_entries:
                oldest = min(self._cached, key=lambda item: self._cached[item][0])
                self._cached.pop(oldest, None)
        return value

    async def cancel_inflight(self) -> None:
        """Cancel outstanding fetches during application shutdown."""
        tasks = list(self._inflight.values())
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._inflight.clear()

    def clear(self) -> None:
        """Drop retained values; active fetches remain owned by their callers."""
        self._cached.clear()


__all__ = ["AsyncKeyedMemo"]
