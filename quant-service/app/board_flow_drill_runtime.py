"""The minute pass that names the members driving each moving licensed board.

Computes only and sends nothing; hands the shortlist to paper auto-execution.
Moved out of main.py (docs/decisions/0008).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from .board_flow_drill import attach_names, drill_board_events
from .xiaojie_reference_repository import instrument_names, sector_membership

SHANGHAI = ZoneInfo("Asia/Shanghai")


@dataclass(frozen=True)
class BoardDrillDependencies:
    database: Any
    run_database: Callable[..., Awaitable[Any]]
    all_a_snapshot: Callable[[], Awaitable[tuple[list[dict[str, Any]], Any]]]
    paper_execution: Callable[[list[dict[str, Any]], dict[str, Any] | None], Awaitable[dict[str, Any]]]
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)


async def drill_board_stock_candidates(
    rotation_events: list[dict[str, Any]], deps: BoardDrillDependencies,
) -> dict[str, Any]:
    """Name the members driving each moving board. Computes only; sends nothing.

    Membership is read for the session being scanned, so it obeys the same
    point-in-time rule as every other reference: a map learned after the open
    cannot inform the session it opened into.
    """

    events = [event for event in rotation_events
              if str(event.get("taxonomy_key") or "") == "longhu_ths_industry"]
    if not events:
        # Held paper positions still need their stops checked on a quiet minute.
        return {"status": "idle", "reason": "no licensed board crossed its threshold", "candidates": [],
                "paper": await deps.paper_execution([], None)}
    trading_date = deps.now().astimezone(SHANGHAI).date()

    def reference() -> tuple[dict[str, set[str]], dict[str, str]]:
        with deps.database.transaction() as connection:
            return sector_membership(connection, trading_date), instrument_names(connection)

    membership, names = await deps.run_database(reference, timeout_seconds=120)
    if not membership:
        return {"status": "blocked", "reason": "no sector membership is known for this session",
                "boards": len(events), "candidates": [], "paper": await deps.paper_execution([], None)}
    rows, _status = await deps.all_a_snapshot()
    quotes = {str(row["symbol"]): row for row in rows if row.get("symbol")}
    drilled = drill_board_events(events, membership, quotes)
    candidates = attach_names(drilled["candidates"], names)
    paper = await deps.paper_execution(candidates, quotes)
    return {
        "status": "completed",
        "boards": len(events),
        "boards_drilled": drilled["boards_drilled"],
        "boards_without_membership": drilled["boards_without_membership"],
        "candidates": candidates,
        "paper": paper,
        "decision_eligible": False,
    }


__all__ = ["BoardDrillDependencies", "drill_board_stock_candidates"]
