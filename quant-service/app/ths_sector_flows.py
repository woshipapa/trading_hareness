"""Daily board flow and concept limit strength, materialized from captured evidence.

Tushare's ``moneyflow_ind_ths``, ``moneyflow_cnt_ths`` and ``limit_cpt_list``
filled ``ths_industry``, ``ths_concept_flow`` and ``ths_limit_strength`` until
decision 0005.  Fuyao serves no THS board net flow, so each is replaced by
evidence this service already captures, kept under that vendor's own
taxonomy and never presented as THS:

* industry flow - the Longhu full-market close's industry boards
  (``longhu_ths_industry``, ``longhuvip_composite``; net inflow in yuan,
  order-size classified, where THS reported 亿元);
* concept flow - the closing-window snapshot of the one-minute Eastmoney
  concept board capture (``eastmoney_concept``; net inflow in 亿元).  Its
  board keys are that capture's, not THS concept codes, so it is never joined
  to a THS concept;
* concept limit strength - sealed members per THS concept, counted from the
  captured Fuyao limit-up pool and the stored ``fuyao_ths_concept`` membership
  (``fuyao_ths_concept_limit_strength``), not THS's own list.

Every result names the taxonomy it wrote and the THS taxonomy it replaces.
No provider is called; a source with nothing for the session is reported, not
written as an empty success.
"""

from __future__ import annotations

from datetime import date, datetime
from functools import partial
from typing import Any, Awaitable, Callable

from .close_board_flow_repository import (
    LONGHU_PROVIDER_KEY, concept_close_snapshot, longhu_close_boards, persist_board_observations,
)
from .concept_limit_candidate_repository import concept_memberships, latest_limit_up_pool
from .concept_limit_strength import concept_strength


#: About 400 concepts a session; the executor's 10s default is not enough.
CONCEPT_PERSIST_TIMEOUT_SECONDS = 120.0
INDUSTRY_TAXONOMY = "longhu_ths_industry"
CONCEPT_FLOW_TAXONOMY = "eastmoney_concept"
STRENGTH_TAXONOMY = "fuyao_ths_concept_limit_strength"


async def sync_industry(
    request: Any,
    *,
    trade_date: Callable[[], date],
    run_database_blocking: Callable[..., Awaitable[Any]],
    db: Any,
) -> dict[str, Any]:
    """Materialize the session's Longhu industry boards as daily observations."""
    day = request.trade_date or trade_date()
    base = {"trade_date": str(day), "taxonomy_key": INDUSTRY_TAXONOMY, "requested_taxonomy_key": "ths_industry",
            "provider": LONGHU_PROVIDER_KEY, "source": "longhuvip_composite:intraday_board_reports"}
    observed_at, boards = await run_database_blocking(longhu_close_boards, db, day)
    rows = [{
        "sector_key": str(board["sector_key"]), "label": board.get("label"), "change_pct": board.get("change_pct"),
        "net_amount": board.get("net_inflow"), "constituent_count": board.get("mapped_members"),
        "raw": {**board, "net_amount_unit": "yuan", "report_observed_at": observed_at.isoformat() if observed_at else None},
    } for board in boards if board.get("sector_key") and board.get("net_inflow") is not None]
    if observed_at is None or not rows:
        return {**base, "status": "blocked", "sectors": 0,
                "reason": "no completed Longhu full-market close board report with board net inflow for the session"
                if observed_at is None or boards else "the Longhu close board report holds no boards"}
    stored = await run_database_blocking(partial(
        persist_board_observations, db, taxonomy_key=INDUSTRY_TAXONOMY, taxonomy_label="开盘啦行业板块",
        provider_key=LONGHU_PROVIDER_KEY, trade_date=day, available_at=observed_at, rows=rows,
        owns_taxonomy=False, taxonomy_metadata={"semantic": "industry_membership"},
    ))
    return {**base, "status": "completed", "sectors": stored, "report_observed_at": observed_at.isoformat(),
            "units": {"net_amount": "yuan"}, "flow_semantics": "order_size_classified_not_institution_identity"}


async def _concept_flow(day: date, run_database_blocking: Callable[..., Awaitable[Any]], db: Any) -> dict[str, Any]:
    base = {"taxonomy_key": CONCEPT_FLOW_TAXONOMY, "requested_taxonomy_key": "ths_concept_flow",
            "source": "intraday_board_flow_snapshots"}
    snapshot_at, items, context = await run_database_blocking(concept_close_snapshot, db, day)
    if snapshot_at is None or not items:
        return {**base, "status": "unavailable", "sectors": 0, **context,
                "reason": "no Eastmoney concept board snapshot in the session's closing window (14:55-15:00)"}
    unit = context.get("unit") or "100m_cny"
    rows = [{
        "sector_key": str(item["sector_key"]), "label": item.get("label"), "change_pct": item.get("change_pct"),
        "net_amount": item.get("net_inflow"),
        "raw": {**item, "net_amount_unit": unit, "snapshot_observed_at": snapshot_at.isoformat()},
    } for item in items]
    stored = await run_database_blocking(partial(
        persist_board_observations, db, taxonomy_key=CONCEPT_FLOW_TAXONOMY, taxonomy_label="东方财富概念板块",
        provider_key=context["provider"], trade_date=day, available_at=snapshot_at, rows=rows,
        owns_taxonomy=False, taxonomy_metadata={"source": "eastmoney", "kind": "concept"},
    ), timeout_seconds=CONCEPT_PERSIST_TIMEOUT_SECONDS)
    return {**base, "status": "completed", "sectors": stored, "provider": context["provider"],
            "snapshot_observed_at": snapshot_at.isoformat(), "units": {"net_amount": unit}}


async def _limit_strength(day: date, run_database_blocking: Callable[..., Awaitable[Any]], db: Any,
                          now_utc: Callable[[], datetime]) -> dict[str, Any]:
    base = {"taxonomy_key": STRENGTH_TAXONOMY, "requested_taxonomy_key": "ths_limit_strength",
            "membership_taxonomy_key": "fuyao_ths_concept", "provider": "fuyao_ths",
            "source": "market_events:limit_up_pool x fuyao_ths_concept"}
    _date, snapshot_at, pool = await run_database_blocking(latest_limit_up_pool, db, day)
    if snapshot_at is None or not pool:
        return {**base, "status": "unavailable", "sectors": 0,
                "reason": "no Fuyao limit-up pool snapshot was captured for the session"}
    memberships, member_counts, labels = await run_database_blocking(concept_memberships, db, day, sorted(pool))
    strength = concept_strength(pool, memberships, member_counts, labels)
    if not strength:
        return {**base, "status": "unavailable", "sectors": 0, "limit_up_symbols": len(pool),
                "reason": "no point-in-time fuyao_ths_concept membership covers the session's limit-up pool"}
    rows = [{
        "sector_key": item["sector_key"], "label": item["label"], "constituent_count": item["member_count"],
        "leading_symbol": item["limit_up_symbols"][0],
        "leading_label": pool[item["limit_up_symbols"][0]].get("name"),
        "raw": {**item, "snapshot_at": snapshot_at.isoformat(),
                "limit_up_names": {symbol: pool[symbol].get("name") for symbol in item["limit_up_symbols"]}},
    } for item in strength]
    stored = await run_database_blocking(partial(
        persist_board_observations, db, taxonomy_key=STRENGTH_TAXONOMY,
        taxonomy_label="同花顺概念涨停强度（Fuyao 涨停池 × 概念成分，自算）", provider_key="fuyao_ths",
        trade_date=day, available_at=now_utc(), rows=rows, owns_taxonomy=True,
        taxonomy_metadata={"derivation": "fuyao_limit_up_pool_x_fuyao_ths_concept",
                           "membership_taxonomy_key": "fuyao_ths_concept", "semantic": "limit_up_strength"},
    ), timeout_seconds=CONCEPT_PERSIST_TIMEOUT_SECONDS)
    return {**base, "status": "completed", "sectors": stored, "limit_up_symbols": len(pool),
            "snapshot_at": snapshot_at.isoformat()}


async def sync_concept_signals(
    request: Any,
    *,
    trade_date: Callable[[], date],
    run_database_blocking: Callable[..., Awaitable[Any]],
    db: Any,
    now_utc: Callable[[], datetime],
) -> dict[str, Any]:
    """Concept flow and concept limit strength for one session, each on its own.

    Either can be missing without the other; the result says which, and is
    ``blocked`` only when neither could be written.
    """
    day = request.trade_date or trade_date()
    results: dict[str, dict[str, Any]] = {}
    for name, action in (("concept_flow", lambda: _concept_flow(day, run_database_blocking, db)),
                         ("limit_strength", lambda: _limit_strength(day, run_database_blocking, db, now_utc))):
        try:
            results[name] = await action()
        except Exception as error:  # noqa: BLE001 - reported per source; the other still runs
            results[name] = {"status": "failed", "sectors": 0, "error": str(getattr(error, "detail", error))[:200]}
    completed = [item for item in results.values() if item["status"] == "completed"]
    status = "completed" if len(completed) == len(results) else "partial" if completed else "blocked"
    return {"status": status, "trade_date": str(day), "sources": results}


__all__ = [
    "CONCEPT_FLOW_TAXONOMY", "CONCEPT_PERSIST_TIMEOUT_SECONDS", "INDUSTRY_TAXONOMY", "STRENGTH_TAXONOMY",
    "sync_concept_signals", "sync_industry",
]
