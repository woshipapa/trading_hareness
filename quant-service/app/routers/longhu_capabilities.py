"""Authenticated, research-only Longhu capability discovery endpoints."""

from __future__ import annotations

import secrets
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import APIRouter, Header, HTTPException

from ..licensed_stock_api import UpstreamStockApiError, catalog
from ..longhu_capability_probe import sanitized_request, summarize_result
from .licensed_stock_api import StockApiCall


def build_longhu_capabilities_router(
    *,
    configured: Callable[[], bool],
    shared_read_key: Callable[[], str],
    call: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]],
) -> APIRouter:
    router = APIRouter(prefix="/api/v1/research/longhu", tags=["longhu-research"])

    def authorize(supplied: str | None) -> None:
        expected = shared_read_key().strip()
        if not expected:
            raise HTTPException(status_code=503, detail="shared licensed read gateway is disabled")
        if not supplied or not secrets.compare_digest(supplied, expected):
            raise HTTPException(status_code=401, detail="valid X-Quant-Read-Key is required")
        if not configured():
            raise HTTPException(status_code=503, detail="Longhu provider is not configured")

    @router.get("/catalog")
    async def capability_catalog(
        x_quant_read_key: str | None = Header(default=None, alias="X-Quant-Read-Key"),
    ) -> dict[str, Any]:
        authorize(x_quant_read_key)
        payload = catalog()
        payload["research_only"] = True
        payload["replay_only"] = True
        payload["live_effect"] = "none"
        return payload

    @router.post("/probe")
    async def probe(
        request: StockApiCall,
        x_quant_read_key: str | None = Header(default=None, alias="X-Quant-Read-Key"),
    ) -> dict[str, Any]:
        authorize(x_quant_read_key)
        request_payload = request.model_dump()
        try:
            result = await call(request_payload)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        except UpstreamStockApiError as error:
            raise HTTPException(status_code=502, detail=str(error)) from error
        return {
            "request": sanitized_request(request_payload),
            "observation": summarize_result(result),
        }

    return router


__all__ = ["build_longhu_capabilities_router"]
