"""Read-only owner storage-layout diagnostics."""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from fastapi import APIRouter

from ..owner_storage import owner_runtime_schema_status


def build_owner_storage_router(
    database: Any,
    run_database: Callable[..., Awaitable[Any]],
) -> APIRouter:
    router = APIRouter(tags=["owner-storage"])

    @router.get("/api/v1/research/storage-tiers")
    async def storage_tiers() -> dict[str, Any]:
        return await run_database(lambda: _read(database))

    return router


def _read(database: Any) -> dict[str, Any]:
    with database.transaction() as connection:
        return owner_runtime_schema_status(connection)


__all__ = ["build_owner_storage_router"]
