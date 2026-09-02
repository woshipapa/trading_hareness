"""Internal, authenticated hand-off for edge raw overflow archiving."""

from __future__ import annotations

from dataclasses import dataclass
from functools import partial
from typing import Any, Awaitable, Callable

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from ..security import raw_overflow_archive_allowed


class RawOverflowOffset(BaseModel):
    effective_at: str
    observation_id: str


class RawOverflowAckRequest(BaseModel):
    batch_id: str
    stream_key: str
    before_offset: RawOverflowOffset | None = None
    first_offset: RawOverflowOffset
    last_offset: RawOverflowOffset
    row_count: int = Field(gt=0, le=2_000)
    compressed_bytes: int = Field(gt=0, le=256 * 1024 * 1024)
    sha256: str = Field(pattern=r"^[0-9a-fA-F]{64}$")
    remote_path: str | None = Field(default=None, max_length=500)
    remote_fs_id: str | None = Field(default=None, max_length=120)


class RawOverflowFailureRequest(BaseModel):
    stream_key: str
    error: str = Field(min_length=1, max_length=500)


@dataclass(frozen=True)
class RawOverflowDependencies:
    database: Any
    config: Callable[[], Any]
    status: Callable[..., dict[str, Any]]
    next_batch: Callable[..., dict[str, Any]]
    acknowledge: Callable[..., dict[str, Any]]
    failure: Callable[..., dict[str, Any]]
    run_database_blocking: Callable[..., Awaitable[Any]]
    configured_key: Callable[[], str]


def build_raw_overflow_router(deps: RawOverflowDependencies) -> APIRouter:
    router = APIRouter(tags=["raw-overflow"])
    prefix = "/api/v1/internal/raw-overflow"

    def authorize(request: Request) -> None:
        if not raw_overflow_archive_allowed(request, deps.configured_key()):
            raise HTTPException(status_code=401, detail="raw overflow archive authorization required")

    @router.get(f"{prefix}/status")
    async def raw_overflow_status(request: Request) -> dict[str, Any]:
        authorize(request)
        return await deps.run_database_blocking(partial(deps.status, deps.database, config=deps.config()), timeout_seconds=20)

    @router.get(f"{prefix}/next")
    async def raw_overflow_next(request: Request, stream_key: str, limit: int = 500) -> dict[str, Any]:
        authorize(request)
        try:
            return await deps.run_database_blocking(
                partial(deps.next_batch, deps.database, stream=stream_key, limit=limit, config=deps.config()), timeout_seconds=30,
            )
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    @router.post(f"{prefix}/ack")
    async def raw_overflow_ack(request: Request, payload: RawOverflowAckRequest) -> dict[str, Any]:
        authorize(request)
        try:
            return await deps.run_database_blocking(
                partial(deps.acknowledge, deps.database, payload=payload.model_dump(mode="json"), config=deps.config()), timeout_seconds=30,
            )
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @router.post(f"{prefix}/failure")
    async def raw_overflow_failure(request: Request, payload: RawOverflowFailureRequest) -> dict[str, Any]:
        authorize(request)
        try:
            return await deps.run_database_blocking(
                partial(deps.failure, deps.database, payload=payload.model_dump(mode="json"), config=deps.config()), timeout_seconds=20,
            )
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    return router


__all__ = ["RawOverflowAckRequest", "RawOverflowDependencies", "build_raw_overflow_router"]
