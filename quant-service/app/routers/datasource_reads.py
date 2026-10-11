"""Research-only reads of package TDX datasource adapters."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ..datasources.adapter_calls import AdapterCallError, read_adapter


class DatasourceReadResponse(BaseModel):
    source: str
    capability: str
    status: str
    decision_eligible: bool
    decision_eligible_reason: str
    params: dict[str, Any]
    started_at: datetime
    finished_at: datetime
    coverage: float | None
    effective_at_min: Any | None
    effective_at_max: Any | None
    available_at_min: Any | None
    available_at_max: Any | None
    warnings: list[str]
    rows: list[dict[str, Any]]
    truncated: bool


def build_datasource_reads_router() -> APIRouter:
    router = APIRouter(tags=["datasource-research-reads"])

    @router.get("/api/v1/datasources/read/{source}/{capability}", response_model=DatasourceReadResponse)
    async def datasource_read(source: str, capability: str, request: Request) -> dict[str, Any]:
        grouped: dict[str, list[str]] = {}
        for name, value in request.query_params.multi_items():
            grouped.setdefault(name, []).append(value)
        try:
            return await read_adapter(source, capability, grouped)
        except AdapterCallError as error:
            raise HTTPException(status_code=error.status_code, detail=error.detail) from error

    return router


__all__ = ["build_datasource_reads_router"]
