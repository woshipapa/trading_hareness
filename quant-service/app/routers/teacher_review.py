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

    return router


__all__ = ["TeacherReviewRouterDependencies", "build_teacher_review_router"]
