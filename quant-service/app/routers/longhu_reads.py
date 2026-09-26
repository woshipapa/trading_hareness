"""Authenticated, bounded read gateway for the licensed Longhu adapter."""

from __future__ import annotations

import gzip
import json
import secrets
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Query, Request, Response

# A 100-symbol minute basket is ~2 MB of numeric JSON; it crosses the SSH
# tunnel every scan, so compress it when the client accepts gzip.
GZIP_MIN_BYTES = 64 * 1024


def _json_response(payload: dict[str, Any], accept_encoding: str) -> Response:
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str).encode("utf-8")
    if len(body) >= GZIP_MIN_BYTES and "gzip" in accept_encoding.lower():
        return Response(gzip.compress(body, compresslevel=5), media_type="application/json",
                        headers={"Content-Encoding": "gzip", "Vary": "Accept-Encoding"})
    return Response(body, media_type="application/json")


def build_longhu_reads_router(
    *,
    configured: Callable[[], bool],
    shared_read_key: Callable[[], str],
    quotes: Callable[[list[str], int], Awaitable[tuple[list[dict[str, Any]], dict[str, Any]]]],
    minutes: Callable[[str], Awaitable[list[dict[str, Any]]]],
    minutes_batch: Callable[[list[str], float], Awaitable[dict[str, Any]]] | None = None,
) -> APIRouter:
    """Expose licensed evidence without distributing the upstream token."""
    router = APIRouter(prefix="/licensed/longhu", tags=["licensed-longhu"])

    def authorize(supplied: str | None) -> None:
        expected = shared_read_key().strip()
        if not expected:
            raise HTTPException(status_code=503, detail="shared licensed read gateway is disabled")
        if not supplied or not secrets.compare_digest(supplied, expected):
            raise HTTPException(status_code=401, detail="valid X-Quant-Read-Key is required")
        if not configured():
            raise HTTPException(status_code=503, detail="Longhu provider is not configured")

    @router.get("/quotes")
    async def read_quotes(
        symbols: str = Query(..., min_length=6, max_length=4_000),
        x_quant_read_key: str | None = Header(default=None, alias="X-Quant-Read-Key"),
    ) -> dict[str, Any]:
        authorize(x_quant_read_key)
        requested = list(dict.fromkeys(item.strip().upper() for item in symbols.split(",") if item.strip()))
        if not requested:
            raise HTTPException(status_code=422, detail="at least one symbol is required")
        if len(requested) > 300:
            raise HTTPException(status_code=422, detail="one logical gateway request is capped at 300 symbols")
        rows, status = await quotes(requested, 300)
        return {"rows": rows, "source_status": status, "physical_request_limit": 300}

    if minutes_batch is not None:
        @router.get("/minutes")
        async def read_minutes_batch(
            request: Request,
            symbols: str = Query(..., min_length=6, max_length=4_000),
            deadline_seconds: float = Query(5.5, ge=1.0, le=20.0),
            x_quant_read_key: str | None = Header(default=None, alias="X-Quant-Read-Key"),
        ) -> Response:
            """Current-session minute paths for up to 300 symbols in one request.

            The single-``StockID`` trend calls fan out on the adapter's own
            pool inside one blocking-executor slot, like ``/quotes``.  Symbols
            still unfinished at the deadline come back as
            ``minute_batch_deadline_exceeded`` instead of holding the request.
            """
            authorize(x_quant_read_key)
            requested = list(dict.fromkeys(item.strip().upper() for item in symbols.split(",") if item.strip()))
            if not requested:
                raise HTTPException(status_code=422, detail="at least one symbol is required")
            if len(requested) > 300:
                raise HTTPException(status_code=422, detail="one logical gateway request is capped at 300 symbols")
            batch = await minutes_batch(requested, deadline_seconds)
            rows = {symbol: value for symbol, value in batch.items() if isinstance(value, list)}
            errors = {symbol: str(value) for symbol, value in batch.items() if not isinstance(value, list)}
            errors.update({symbol: "minute_batch_missing_symbol" for symbol in requested
                           if symbol not in rows and symbol not in errors})
            return _json_response({
                "rows": rows, "errors": errors, "requested": len(requested), "completed": len(rows),
                "deadline_seconds": deadline_seconds, "session_guard": "current_exchange_session",
                "source": "longhuvip:GetStockTrendIncremental", "physical_request_limit": 300,
            }, request.headers.get("accept-encoding", ""))

    @router.get("/minutes/{symbol}")
    async def read_minutes(
        symbol: str,
        x_quant_read_key: str | None = Header(default=None, alias="X-Quant-Read-Key"),
    ) -> dict[str, Any]:
        authorize(x_quant_read_key)
        rows = await minutes(symbol.upper())
        return {
            "symbol": symbol.upper(), "rows": rows,
            "source": "longhuvip:GetStockTrendIncremental", "physical_request_limit": 300,
        }

    return router


__all__ = ["build_longhu_reads_router"]
