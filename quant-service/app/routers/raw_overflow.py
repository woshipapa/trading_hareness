"""Internal, authenticated hand-off for edge raw overflow archiving."""

from __future__ import annotations

from dataclasses import dataclass
from functools import partial
import logging
from typing import Any, Awaitable, Callable

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from ..security import raw_overflow_archive_allowed


LOGGER = logging.getLogger(__name__)


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
    # The parsed write credentials (or, in tests, the single legacy key).
    configured_key: Callable[[], Any]


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
            LOGGER.error("raw overflow next rejected stream=%s limit=%s error=%s", stream_key, limit, error, exc_info=True)
            raise HTTPException(status_code=400, detail=str(error)) from error
        except Exception as error:  # noqa: BLE001 - boundary must expose durable evidence
            LOGGER.exception("raw overflow next database failure stream=%s limit=%s", stream_key, limit)
            raise HTTPException(status_code=503, detail="raw overflow source unavailable") from error

    @router.post(f"{prefix}/ack")
    async def raw_overflow_ack(request: Request, payload: RawOverflowAckRequest) -> dict[str, Any]:
        authorize(request)
        try:
            return await deps.run_database_blocking(
                partial(deps.acknowledge, deps.database, payload=payload.model_dump(mode="json"), config=deps.config()), timeout_seconds=30,
            )
        except ValueError as error:
            LOGGER.error("raw overflow ack rejected stream=%s batch=%s error=%s", payload.stream_key, payload.batch_id, error, exc_info=True)
            raise HTTPException(status_code=409, detail=str(error)) from error
        except Exception as error:  # noqa: BLE001
            LOGGER.exception("raw overflow ack database failure stream=%s batch=%s", payload.stream_key, payload.batch_id)
            raise HTTPException(status_code=503, detail="raw overflow source unavailable") from error

    @router.post(f"{prefix}/failure")
    async def raw_overflow_failure(request: Request, payload: RawOverflowFailureRequest) -> dict[str, Any]:
        authorize(request)
        try:
            return await deps.run_database_blocking(
                partial(deps.failure, deps.database, payload=payload.model_dump(mode="json"), config=deps.config()), timeout_seconds=20,
            )
        except ValueError as error:
            LOGGER.error("raw overflow failure report rejected stream=%s error=%s", payload.stream_key, error, exc_info=True)
            raise HTTPException(status_code=400, detail=str(error)) from error
        except Exception as error:  # noqa: BLE001
            LOGGER.exception("raw overflow failure report database failure stream=%s", payload.stream_key)
            raise HTTPException(status_code=503, detail="raw overflow source unavailable") from error

    return router


__all__ = ["RawOverflowAckRequest", "RawOverflowDependencies", "build_raw_overflow_router"]
