"""HTTP boundary for the post-close watched-stock reviews (research only)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date
from typing import Any

from fastapi import APIRouter


@dataclass(frozen=True)
class WatchReviewRouterDependencies:
    run: Callable[[date, bool], Awaitable[dict[str, Any]]]
    read: Callable[[date], Awaitable[list[dict[str, Any]]]]
    today: Callable[[], date]


def build_watch_review_router(dependencies: WatchReviewRouterDependencies) -> APIRouter:
    router = APIRouter(tags=["watch-reviews"])

    @router.get("/api/v1/watch-reviews")
    async def read_reviews(trade_date: date | None = None) -> dict[str, Any]:
        day = trade_date or dependencies.today()
        rows = await dependencies.read(day)
        summary = next((row for row in rows if row.get("symbol") is None and "patterns" in row and "stocks" in row), None)
        return {"trade_date": day.isoformat(), "summary": summary,
                "reviews": [row for row in rows if row is not summary]}

    @router.post("/api/v1/watch-reviews/run")
    async def run_reviews(trade_date: date | None = None, persist: bool = True) -> dict[str, Any]:
        return await dependencies.run(trade_date or dependencies.today(), persist)

    return router


__all__ = ["WatchReviewRouterDependencies", "build_watch_review_router"]
