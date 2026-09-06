"""Read-only Longhu supplemental replay evidence endpoints."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter

from ..runtime_executors import run_database_blocking


def build_longhu_replay_reads_router(readiness: Callable[[], dict[str, Any]]) -> APIRouter:
    router = APIRouter(prefix="/api/v1/research/longhu", tags=["longhu-research"])

    @router.get("/replay-readiness")
    async def replay_readiness() -> dict[str, Any]:
        return await run_database_blocking(readiness)

    return router


__all__ = ["build_longhu_replay_reads_router"]
