"""THS concept limit-up candidates from the captured Fuyao pool.

The Tushare version selected the concepts with the largest positive THS
concept money flow (``moneyflow_cnt_ths``), fetched their ``ths_member``
snapshots and joined the day's ``limit_list_ths`` pool.  None of that is
available since decision 0005, and Fuyao serves no THS board net flow.  The
replacement makes no provider call at all: the session's last captured Fuyao
limit-up pool is joined, by exact code, to the stored point-in-time
``fuyao_ths_concept`` membership, and the concepts holding the most sealed
members are the candidates' concepts.  Concepts are therefore ranked by
limit-up strength, not by money flow.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, time, timezone
from typing import Any
from zoneinfo import ZoneInfo

from .concept_limit_candidate_repository import (
    PROVIDER_KEY, TAXONOMY_KEY, concept_memberships, latest_limit_up_pool, persist_candidates,
)
from .concept_limit_strength import concept_strength


SOURCE = "fuyao_ths:limit_up_pool x fuyao_ths_concept"
#: The capture runs until 15:00; a snapshot before this is not the session's close.
CLOSE_SNAPSHOT_FROM = time(14, 57)


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class ConceptLimitCandidateDependencies:
    run_database: Callable[..., Awaitable[Any]]
    database: Any
    now_utc: Callable[[], datetime] = _now_utc


async def run(request: Any, dependencies: ConceptLimitCandidateDependencies) -> dict[str, Any]:
    """Build candidates from stored evidence only; fail closed on a gap."""
    base: dict[str, Any] = {"taxonomy_key": TAXONOMY_KEY, "requested_taxonomy_key": "ths_concept_flow",
                            "source": SOURCE, "limit_provider": PROVIDER_KEY, "decision_eligible": False}
    trade_date, snapshot_at, pool = await dependencies.run_database(
        latest_limit_up_pool, dependencies.database, request.trade_date,
    )
    if trade_date is None or snapshot_at is None or not pool:
        return {**base, "status": "blocked", "trade_date": str(trade_date) if trade_date else None,
                "reason": "no Fuyao limit-up pool snapshot was captured for the session"}
    memberships, member_counts, labels = await dependencies.run_database(
        concept_memberships, dependencies.database, trade_date, sorted(pool),
    )
    if not memberships:
        return {**base, "status": "blocked", "trade_date": str(trade_date), "limit_rows": len(pool),
                "reason": "no point-in-time fuyao_ths_concept membership covers the session's limit-up pool"}
    ranked = concept_strength(pool, memberships, member_counts, labels)
    concepts = ranked[:request.top_concepts]
    stored, per_concept = await dependencies.run_database(
        persist_candidates, dependencies.database, trade_date, snapshot_at, concepts, pool,
        request.leaders_per_concept, dependencies.now_utc(),
    )
    session_closed = snapshot_at.astimezone(ZoneInfo("Asia/Shanghai")).time() >= CLOSE_SNAPSHOT_FROM
    return {
        **base, "status": "completed", "trade_date": str(trade_date), "concepts": per_concept,
        "candidates": stored, "limit_rows": len(pool), "concepts_with_limit_ups": len(ranked),
        "limit_snapshot_at": snapshot_at.isoformat(), "limit_snapshot_is_close": session_closed,
        "selection": "concepts ranked by sealed point-in-time members (limit-up strength); THS concept money flow is unavailable",
        "matching_rule": "same-session fuyao_ths_concept member code equals Fuyao limit-up-pool stock code",
    }


__all__ = ["CLOSE_SNAPSHOT_FROM", "ConceptLimitCandidateDependencies", "SOURCE", "run"]
