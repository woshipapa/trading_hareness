"""Operational health, metrics and legacy-bootstrap route assembly.

These endpoints deliberately have no market-provider dependency.  Keeping
their HTTP declarations outside the application composition root makes that
boundary explicit while leaving the root responsible for injecting local
runtime state.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from fastapi import APIRouter, HTTPException
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response

#: Longest a health probe waits for the full evidence before answering from
#: memory.  The container's urllib probe has a three-second socket deadline,
#: so the in-app fallback must leave enough time to serialize its busy answer.
HEALTH_DEADLINE_SECONDS = 1.0
#: How long the service may keep answering "busy" before health fails anyway,
#: so a process that is truly wedged is still restarted.
BUSY_GRACE_SECONDS = 180.0


class BusyTracker:
    """When the service first stopped completing its full health evidence."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._since: float | None = None

    def busy_for(self) -> float:
        now = self._clock()
        if self._since is None:
            self._since = now
        return now - self._since

    def clear(self) -> None:
        self._since = None


@dataclass(frozen=True)
class SystemControlDependencies:
    health_payload: Callable[[], dict[str, Any]]
    database_unavailable_error: type[Exception]
    metrics_response: Callable[[], Response]
    legacy_bootstrap: Callable[[], dict[str, Any]]
    #: In-memory state only - no database, no pool - returned when the full
    #: evidence did not finish in time.  Without it a slow probe fails as before.
    busy_payload: Callable[[], dict[str, Any]] | None = None
    health_deadline_seconds: float = HEALTH_DEADLINE_SECONDS
    busy_grace_seconds: float = BUSY_GRACE_SECONDS
    busy_tracker: BusyTracker = field(default_factory=BusyTracker)


def build_system_control_router(deps: SystemControlDependencies) -> APIRouter:
    """Expose only local operational controls with injected behavior."""
    router = APIRouter(tags=["system-control"])
    in_flight: list[asyncio.Future[dict[str, Any]]] = []

    def evidence() -> asyncio.Future[dict[str, Any]]:
        """One computation at a time: probes that arrive while it runs share it."""
        if in_flight and not in_flight[0].done():
            return in_flight[0]
        work = asyncio.ensure_future(run_in_threadpool(deps.health_payload))
        # A probe that gave up must not leave an unobserved exception behind.
        work.add_done_callback(lambda done: done.cancelled() or done.exception())
        in_flight[:] = [work]
        return work

    @router.get("/health")
    async def health() -> dict[str, Any]:
        """Full evidence when it is quick; a live "busy" answer when it is not.

        On 2026-09-23 the guard restarted the API three times in one session
        (10:16, 11:22, 13:00).  Each time the process was alive - 13 of 13
        /health calls in the four minutes before 13:00 returned 200 - but one
        probe outlasted the guard's 15 s while a dashboard's parallel reads and
        the session-open loops held every pool connection.  A restart cures
        none of that and drops a minute of capture, so a slow probe now answers
        from memory.  A service that stays that busy for ``busy_grace_seconds``
        still fails health, and an unreachable database still fails at once.
        """
        work = evidence()
        try:
            payload = await asyncio.wait_for(asyncio.shield(work), timeout=deps.health_deadline_seconds)
        except asyncio.TimeoutError:
            busy_for = deps.busy_tracker.busy_for()
            if deps.busy_payload is None or busy_for > deps.busy_grace_seconds:
                raise HTTPException(status_code=503, detail=(
                    f"health evidence has not completed within {deps.health_deadline_seconds:g}s "
                    f"for {busy_for:.0f}s")) from None
            return {**deps.busy_payload(), "status": "busy", "busy_for_seconds": round(busy_for, 1),
                    "busy_reason": (f"full health evidence did not complete within "
                                    f"{deps.health_deadline_seconds:g}s; answered from memory")}
        except deps.database_unavailable_error as error:
            raise HTTPException(status_code=503, detail=f"database unavailable: {error}") from error
        deps.busy_tracker.clear()
        return payload

    @router.get("/metrics", include_in_schema=False)
    def prometheus_metrics() -> Response:
        return deps.metrics_response()

    @router.post("/api/v1/bootstrap")
    def bootstrap() -> dict[str, Any]:
        return deps.legacy_bootstrap()

    return router


__all__ = [
    "BUSY_GRACE_SECONDS", "BusyTracker", "HEALTH_DEADLINE_SECONDS", "SystemControlDependencies",
    "build_system_control_router",
]
