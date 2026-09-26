"""HTTP boundary for the research-only 小杰龙头策略 evaluator."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from fastapi import APIRouter, Query

from ..request_models import XiaojieLeaderFlowEvaluateRequest


def build_xiaojie_leader_flow_router(
    evaluate_fn: Callable[[dict[str, Any], dict[str, Any] | None], dict[str, Any]],
    read_message_features: Callable[..., Awaitable[list[dict[str, Any]]]] | None = None,
) -> APIRouter:
    router = APIRouter(tags=["xiaojie-leader-flow"])

    if read_message_features is not None:
        @router.get("/api/v1/research/strategies/xiaojie-leader-flow/message-features")
        async def message_features(
            symbol: str | None = Query(default=None, max_length=24),
            as_of: datetime | None = None,
            since: datetime | None = None,
            instructor_only: bool = False,
            limit: int = Query(default=100, ge=1, le=500),
        ) -> dict[str, Any]:
            """Group-message features known at ``as_of`` (default now), newest first."""
            as_of = as_of or datetime.now(timezone.utc)
            items = await read_message_features(symbol=symbol, as_of=as_of, since=since,
                                                instructor_only=instructor_only, limit=limit)
            return {"as_of": as_of.isoformat(), "count": len(items), "items": items,
                    "availability": "ledger_first_receive_time", "live_effect": "none"}

    @router.post("/api/v1/research/strategies/xiaojie-leader-flow/evaluate")
    def evaluate(payload: XiaojieLeaderFlowEvaluateRequest) -> dict[str, Any]:
        result = evaluate_fn(payload.snapshot.model_dump(exclude_none=True), payload.parameters)
        return {**result, "live_effect": "none", "boundary": "research_only; no_automatic_order"}

    return router


__all__ = ["build_xiaojie_leader_flow_router"]
