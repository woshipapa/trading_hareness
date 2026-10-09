"""Per-provider request admission for public data sources.

The owner is the only realtime writer, so a process-local guard is enough for
these token-free HTTP sources.  Slots start ``60 / rate`` seconds apart.  By
default a caller that arrives before the next slot is rejected immediately;
it must record ``rate_limited`` and use its documented fallback instead of
building an unbounded wait queue.

A caller may instead wait a bounded time for its slot.  Fuyao needs that: the
all-A snapshot is two back-to-back pages and the event capture asks for
several pools at once, so on 2026-10-09 the owner rejected 615 Fuyao calls in
three hours - most minutes' all-A capture failed - while the whole demand was
about 15 calls a minute against a budget of 60.  The pacing is unchanged;
only the burst now queues, and a slot further off than the bound is still
rejected.
"""

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass


class PublicProviderRateLimited(RuntimeError):
    """The provider's reserved local start slot is already occupied."""

    def __init__(self, provider_key: str) -> None:
        super().__init__(f"rate_limited:{provider_key}")
        self.provider_key = provider_key


DEFAULT_RATE_LIMITS_PER_MINUTE = {
    "eastmoney_free": 60,
    "tencent_free": 60,
    "sina_free": 60,
    "fuyao_ths": 60,
    "cninfo_free": 30,
}


def provider_key_for_host(host: str) -> str:
    normalized = str(host or "").lower()
    if "eastmoney" in normalized:
        return "eastmoney_free"
    if "gtimg" in normalized or "qq.com" in normalized:
        return "tencent_free"
    if "sinajs" in normalized or "sina.com" in normalized:
        return "sina_free"
    if "cninfo" in normalized:
        return "cninfo_free"
    return normalized or "public_unknown"


def configured_rate_limit(provider_key: str) -> int:
    env_name = "PUBLIC_RATE_LIMIT_{}_PER_MINUTE".format(provider_key.upper().replace("-", "_"))
    raw = os.getenv(env_name)
    if raw is None:
        return max(1, int(DEFAULT_RATE_LIMITS_PER_MINUTE.get(provider_key, 120)))
    try:
        return max(1, min(600, int(raw)))
    except (TypeError, ValueError):
        return max(1, int(DEFAULT_RATE_LIMITS_PER_MINUTE.get(provider_key, 120)))


@dataclass
class _Slot:
    next_allowed_at: float = 0.0


class PublicProviderRateLimiter:
    """A tiny non-blocking start-slot limiter with deterministic spacing."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._slots: dict[str, _Slot] = {}
        self._loop: asyncio.AbstractEventLoop | None = None

    async def try_acquire(self, provider_key: str, rate_limit_per_minute: int) -> bool:
        spacing = 60.0 / max(1, int(rate_limit_per_minute))
        now = time.monotonic()
        async with self._lock:
            loop = asyncio.get_running_loop()
            # Unit callers often create short-lived event loops with
            # ``asyncio.run``.  Their in-memory pacing state must not leak
            # into the next loop; the owner runtime has one long-lived loop.
            if self._loop is not loop:
                self._slots.clear()
                self._loop = loop
            slot = self._slots.setdefault(provider_key, _Slot())
            if slot.next_allowed_at > now:
                return False
            slot.next_allowed_at = now + spacing
            return True

    async def reserve(self, provider_key: str, rate_limit_per_minute: int, max_wait_seconds: float) -> float | None:
        """Reserve the next start slot: the wait until it, or None when it is further off than allowed."""
        spacing = 60.0 / max(1, int(rate_limit_per_minute))
        now = time.monotonic()
        async with self._lock:
            loop = asyncio.get_running_loop()
            if self._loop is not loop:
                self._slots.clear()
                self._loop = loop
            slot = self._slots.setdefault(provider_key, _Slot())
            start = max(now, slot.next_allowed_at)
            if start - now > max(0.0, max_wait_seconds):
                return None
            slot.next_allowed_at = start + spacing
            return start - now

    async def acquire(self, provider_key: str, rate_limit_per_minute: int, *,
                      max_wait_seconds: float = 0.0) -> None:
        wait = await self.reserve(provider_key, rate_limit_per_minute, max_wait_seconds)
        if wait is None:
            raise PublicProviderRateLimited(provider_key)
        if wait > 0:
            await asyncio.sleep(wait)

    def reset(self) -> None:
        self._slots.clear()


public_provider_rate_limiter = PublicProviderRateLimiter()


async def acquire_public_provider_slot(provider_key: str, *, max_wait_seconds: float = 0.0) -> None:
    await public_provider_rate_limiter.acquire(
        provider_key, configured_rate_limit(provider_key), max_wait_seconds=max_wait_seconds,
    )


__all__ = [
    "DEFAULT_RATE_LIMITS_PER_MINUTE", "PublicProviderRateLimited", "PublicProviderRateLimiter",
    "acquire_public_provider_slot", "configured_rate_limit", "provider_key_for_host",
    "public_provider_rate_limiter",
]
