"""HTTP boundary for analyst/teacher review packs (research only, no orders)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date
from typing import Any

from fastapi import APIRouter, HTTPException

from ..request_models import TeacherReviewPackImportRequest


@dataclass(frozen=True)
class TeacherReviewRouterDependencies:
    enabled: Callable[[], bool]
    import_pack: Callable[..., Awaitable[dict[str, Any]]]
    cohort: Callable[[], Awaitable[dict[str, Any]]]
    settlements: Callable[[int], Awaitable[list[dict[str, Any]]]]
    roll: Callable[[date | None], Awaitable[dict[str, Any]]]
    outcomes: Callable[[int], Awaitable[list[dict[str, Any]]]] | None = None
    outcome_review: Callable[[date | None], Awaitable[dict[str, Any]]] | None = None
    changes: Callable[[int], Awaitable[list[dict[str, Any]]]] | None = None
    record_change: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]] | None = None


def build_teacher_review_router(dependencies: TeacherReviewRouterDependencies) -> APIRouter:
    router = APIRouter(tags=["teacher-review"])

    @router.post("/api/v1/teacher-review/packs")
    async def import_pack(payload: TeacherReviewPackImportRequest) -> dict[str, Any]:
        if not dependencies.enabled():
            raise HTTPException(status_code=503, detail="teacher review is disabled (TEACHER_REVIEW_ENABLED)")
        result = await dependencies.import_pack(payload.pack, dry_run=payload.dry_run)
        if result.get("status") == "rejected":
            raise HTTPException(status_code=422, detail={"problems": result.get("problems") or []})
        return {**result, "live_effect": "none", "boundary": "research_only; no_automatic_order"}

    @router.post("/api/v1/teacher-review/roll")
    async def roll(trade_date: date | None = None) -> dict[str, Any]:
        """Re-run the post-close roll: settle ``trade_date`` and re-freeze next-session plans."""
        if not dependencies.enabled():
            raise HTTPException(status_code=503, detail="teacher review is disabled (TEACHER_REVIEW_ENABLED)")
        return {**await dependencies.roll(trade_date), "live_effect": "none"}

    @router.get("/api/v1/teacher-review/cohort")
    async def cohort() -> dict[str, Any]:
        return {**await dependencies.cohort(), "live_effect": "none"}

    @router.get("/api/v1/teacher-review/settlements")
    async def settlements(limit: int = 10) -> dict[str, Any]:
        return {"items": await dependencies.settlements(limit), "live_effect": "none"}

    @router.get("/api/v1/teacher-review/outcomes")
    async def outcomes(limit: int = 5) -> dict[str, Any]:
        """Archived next-day outcome reviews: what worked, what was missed and what blocked it."""
        if dependencies.outcomes is None:
            raise HTTPException(status_code=503, detail="outcome review is not wired")
        return {"items": await dependencies.outcomes(limit), "live_effect": "none"}

    @router.post("/api/v1/teacher-review/outcome-review")
    async def outcome_review(trade_date: date | None = None) -> dict[str, Any]:
        """Re-run one session's outcome review (idempotent: it re-archives the report)."""
        if not dependencies.enabled():
            raise HTTPException(status_code=503, detail="teacher review is disabled (TEACHER_REVIEW_ENABLED)")
        if dependencies.outcome_review is None:
            raise HTTPException(status_code=503, detail="outcome review is not wired")
        return {**await dependencies.outcome_review(trade_date), "live_effect": "none"}

    @router.get("/api/v1/research/strategy-changes")
    async def strategy_changes(limit: int = 50) -> dict[str, Any]:
        """Every deliberate strategy change with its preregistered expectation."""
        if dependencies.changes is None:
            raise HTTPException(status_code=503, detail="the change log is not wired")
        return {"items": await dependencies.changes(limit), "live_effect": "none"}

    @router.post("/api/v1/research/strategy-changes")
    async def record_strategy_change(payload: dict[str, Any]) -> dict[str, Any]:
        """Record a change before it ships; it is refused without an expectation."""
        if dependencies.record_change is None:
            raise HTTPException(status_code=503, detail="the change log is not wired")
        result = await dependencies.record_change(payload)
        if result.get("status") == "rejected":
            raise HTTPException(status_code=422, detail={"problems": result.get("problems") or []})
        return {**result, "live_effect": "none"}

    return router


__all__ = ["TeacherReviewRouterDependencies", "build_teacher_review_router"]
