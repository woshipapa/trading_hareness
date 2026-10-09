"""Bounded read-only projections for persisted sector, concept, and member evidence.

Tushare's THS concept flow, limit strength and ``ths_member`` backfill were
retired with Tushare (decision 0005).  The concept reads now serve their
replacements -- Eastmoney concept flow, Fuyao-served THS concept candidates
and the Fuyao membership refresh -- and fall back to the THS rows only for a
session that has nothing newer, always naming the taxonomy they served.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Mapping, Sequence


#: Concept flow, replacement first; the THS rows remain readable history.
CONCEPT_FLOW_TAXONOMIES = ("eastmoney_concept", "ths_concept_flow")
#: Only the THS concept flow shares board codes with a limit-strength taxonomy.
STRENGTH_FOR_FLOW = {"ths_concept_flow": "ths_limit_strength"}
CANDIDATE_TAXONOMIES = ("fuyao_ths_concept", "ths_concept_flow")
#: The board flow each candidate taxonomy can be joined to by exact code.
FLOW_FOR_CANDIDATES = {"ths_concept_flow": "ths_concept_flow"}
MEMBERSHIP_TAXONOMIES = ("fuyao_ths_concept", "fuyao_ths_industry", "fuyao_ths_region")
#: THS taxonomies whose Tushare writer is retired, and what replaced each.
SUPERSEDED_TAXONOMIES = {
    "ths_industry": "longhu_ths_industry", "ths_concept_flow": "eastmoney_concept",
    "ths_limit_strength": "fuyao_ths_concept_limit_strength",
}


def select_taxonomy(latest: Mapping[str, date | None], preference: Sequence[str]) -> tuple[str, date | None]:
    """The taxonomy with the newest session; the preferred one on a tie.

    ``latest`` maps each candidate to its newest session (or, for a requested
    date, to that date when it has rows).  Without any rows the preferred
    taxonomy is named with no date.
    """
    dated = [(latest[key], -index, key) for index, key in enumerate(preference) if latest.get(key) is not None]
    if not dated:
        return preference[0], None
    selected = max(dated)
    return selected[2], selected[0]


def superseded_notice(taxonomy_key: str) -> dict[str, str]:
    replacement = SUPERSEDED_TAXONOMIES.get(taxonomy_key)
    if replacement is None:
        return {}
    return {"superseded_by": replacement,
            "notice": f"{taxonomy_key} is no longer written (Tushare retired, decision 0005); "
                      f"newer sessions are under {replacement}, a different vendor's taxonomy."}


def project_concept_member_backfill_status(
    selected_date: date,
    total: int,
    receipt_mapped: int,
    active_mapping: dict[str, Any],
    states: list[Any],
    *,
    automatic_enabled: bool,
    batch_size: int,
) -> dict[str, Any]:
    """Expose actual exact-member coverage separately from sync receipts."""
    active_mapped = int(active_mapping.get("mapped_concepts") or 0)
    active_members = int(active_mapping.get("member_rows") or 0)
    active_available_at = active_mapping.get("latest_available_at")
    items = [dict(row) for row in states]
    if active_mapped and not items:
        items.append({
            "state": "active_exact_mapping", "boards": active_mapped, "members": active_members,
            "evidence_rows": active_members, "latest_updated_at": active_available_at,
        })
    return {
        "trade_date": str(selected_date), "total_concepts": total, "mapped_concepts": active_mapped,
        "complete": active_mapped == total, "states": items,
        "receipt_mapped_concepts": receipt_mapped, "receipt_complete": receipt_mapped == total,
        "mapping_evidence": {
            "status": "current_active_exact" if active_mapped else "missing",
            "latest_available_at": active_available_at,
            "notice": "active exact mapping reflects current effective code relations; receipt coverage only reflects a same-date member-sync ledger.",
        },
        "automatic": {"enabled": automatic_enabled, "batch_size": batch_size},
        "notice": "只有精确成员已完成的概念才可展示完整 Top10；mapped_concepts 使用当前有效精确代码关系，receipt_mapped_concepts 单独披露同日同步回执，二者不互相替代。",
    }


def concept_member_backfill_status(
    database: Any, trade_date: date | None, *, automatic_enabled: bool, batch_size: int,
) -> dict[str, Any]:
    """Fuyao THS membership refresh progress for one refresh date."""
    concept = MEMBERSHIP_TAXONOMIES[0]
    with database.transaction() as connection:
        selected_date = trade_date or connection.execute(
            "SELECT max(trading_date) latest FROM quant.sector_member_sync_state WHERE taxonomy_key=ANY(%s)",
            (list(MEMBERSHIP_TAXONOMIES),),
        ).fetchone()["latest"]
        if selected_date is None:
            return {"trade_date": None, "total_concepts": 0, "mapped_concepts": 0, "states": [],
                    **_membership_source(), "notice": "尚未刷新 Fuyao 同花顺板块成分。"}
        progress = connection.execute(MEMBERSHIP_PROGRESS_SQL, (selected_date, list(MEMBERSHIP_TAXONOMIES),
                                                                  selected_date.isoformat())).fetchall()
        active_mapping = connection.execute(ACTIVE_MAPPING_SQL, (concept, selected_date, concept,
                                                                 selected_date.isoformat())).fetchone()
        states = connection.execute(SYNC_STATES_SQL, (concept, selected_date)).fetchall()
    return project_membership_refresh_status(
        selected_date, [dict(row) for row in progress], dict(active_mapping or {}), states,
        automatic_enabled=automatic_enabled, batch_size=batch_size,
    )


def _membership_source() -> dict[str, Any]:
    return {"taxonomy_key": MEMBERSHIP_TAXONOMIES[0], "taxonomy_keys": list(MEMBERSHIP_TAXONOMIES),
            "requested_taxonomy_key": "ths_concept_flow", "source": "fuyao_ths"}


#: Boards listed, settled and failing per taxonomy for one refresh date.
MEMBERSHIP_PROGRESS_SQL = """
    SELECT s.taxonomy_key,count(*)::int listed,
           count(*) FILTER (WHERE state.state IN ('completed','empty'))::int done,
           count(*) FILTER (WHERE state.state='failed')::int failed
      FROM quant.sectors s
      LEFT JOIN quant.sector_member_sync_state state
        ON state.taxonomy_key=s.taxonomy_key AND state.sector_key=s.sector_key AND state.trading_date=%s
     WHERE s.taxonomy_key=ANY(%s) AND s.metadata->>'listed_on'=%s
     GROUP BY s.taxonomy_key ORDER BY s.taxonomy_key
"""
#: Listed concepts with open members, and the latest confirmation that day.
ACTIVE_MAPPING_SQL = """
    SELECT count(DISTINCT history.sector_key)::int AS mapped_concepts,count(history.symbol)::int AS member_rows,
           (SELECT max(updated_at) FROM quant.sector_member_sync_state
             WHERE taxonomy_key=%s AND trading_date=%s AND state IN ('completed','empty')) AS latest_available_at
      FROM quant.sectors s
      JOIN quant.sector_membership_history history
        ON history.taxonomy_key=s.taxonomy_key AND history.sector_key=s.sector_key AND history.effective_to IS NULL
     WHERE s.taxonomy_key=%s AND s.metadata->>'listed_on'=%s
"""
SYNC_STATES_SQL = """
    SELECT sync.state,count(*)::int boards,coalesce(sum(active.member_count),0)::int members,
           sum(sync.member_count)::int evidence_rows,max(sync.updated_at) latest_updated_at
      FROM quant.sector_member_sync_state sync
      LEFT JOIN LATERAL (
          SELECT count(*)::int member_count FROM quant.sector_membership_history history
           WHERE history.taxonomy_key=sync.taxonomy_key AND history.sector_key=sync.sector_key
             AND history.effective_to IS NULL
      ) active ON true
     WHERE sync.taxonomy_key=%s AND sync.trading_date=%s
     GROUP BY sync.state ORDER BY sync.state
"""


def project_membership_refresh_status(
    selected_date: date,
    progress: list[dict[str, Any]],
    active_mapping: dict[str, Any],
    states: list[Any],
    *,
    automatic_enabled: bool,
    batch_size: int,
) -> dict[str, Any]:
    """Concept coverage in the old shape, plus every Fuyao taxonomy's progress."""
    by_taxonomy = {key: {"listed": 0, "completed_or_empty": 0, "failed": 0} for key in MEMBERSHIP_TAXONOMIES}
    for row in progress:
        by_taxonomy[str(row["taxonomy_key"])] = {
            "listed": int(row["listed"] or 0), "completed_or_empty": int(row["done"] or 0), "failed": int(row["failed"] or 0),
        }
    concept = by_taxonomy[MEMBERSHIP_TAXONOMIES[0]]
    return {
        **project_concept_member_backfill_status(
            selected_date, concept["listed"], concept["completed_or_empty"], active_mapping, states,
            automatic_enabled=automatic_enabled, batch_size=batch_size,
        ),
        **_membership_source(), "taxonomies": by_taxonomy,
        "refresh": "after the close, in batches, from Fuyao ths_index_list / ths_index_constituents",
    }


def latest_sessions_sql(table: str) -> str:
    """Newest session per candidate taxonomy, or the requested session if it has rows.

    ``table`` is one of this module's two fixed relations, never request input.
    """
    return f"""SELECT taxonomy_key,max(trading_date) latest FROM {table}
                WHERE taxonomy_key=ANY(%s) AND (%s::date IS NULL OR trading_date=%s)
                GROUP BY taxonomy_key"""


CONCEPT_SIGNALS_SQL = """
    WITH concept AS (
        SELECT o.sector_key,s.label,o.close,o.change_pct,o.net_amount,o.net_buy_amount,o.net_sell_amount,
               o.constituent_count,o.leading_label,o.provider_key,o.available_at,o.raw,
               percent_rank() OVER (ORDER BY o.net_amount NULLS FIRST) AS flow_percentile
          FROM quant.sector_market_observations o
          JOIN quant.sectors s ON s.taxonomy_key=o.taxonomy_key AND s.sector_key=o.sector_key
         WHERE o.taxonomy_key=%s AND o.trading_date=%s
    )
    SELECT c.*,ls.provider_key strength_provider,ls.raw strength_raw,
           nullif(ls.raw->>'up_nums','')::numeric up_nums,
           nullif(ls.raw->>'cons_nums','')::numeric strength_constituents,
           nullif(ls.raw->>'days','')::numeric streak_days
      FROM concept c
 LEFT JOIN quant.sector_market_observations ls
        ON ls.taxonomy_key=%s AND ls.trading_date=%s AND ls.sector_key=c.sector_key
     ORDER BY c.net_amount DESC NULLS LAST,c.label LIMIT %s
"""
CONCEPT_CANDIDATES_SQL = """
    SELECT c.sector_key,s.label concept_label,c.symbol,c.name,c.limit_tag,c.limit_type,c.pct_change,c.price,c.limit_amount,
           c.turnover_rate,c.open_num,c.status,c.description,c.provider_key,c.available_at,c.taxonomy_key,
           coalesce(c.raw->>'membership_fetch_status','unknown') membership_status,
           flow.net_amount board_net_amount,flow.change_pct board_change_pct,flow.leading_label board_leading_label,
           (c.raw->'concept'->>'limit_up_count')::int board_limit_up_count,
           (c.raw->'concept'->>'rank')::int board_strength_rank
      FROM quant.sector_limit_candidates c
      JOIN quant.sectors s ON s.taxonomy_key=c.taxonomy_key AND s.sector_key=c.sector_key
 LEFT JOIN quant.sector_market_observations flow ON flow.taxonomy_key=%s AND flow.sector_key=c.sector_key
           AND flow.trading_date=c.trading_date
     WHERE c.taxonomy_key=%s AND c.trading_date=%s
     ORDER BY board_strength_rank NULLS LAST,flow.net_amount DESC NULLS LAST,c.limit_amount DESC NULLS LAST,c.symbol
     LIMIT %s
"""


def _latest_sessions(connection: Any, table: str, taxonomies: Sequence[str], trade_date: date | None) -> dict[str, Any]:
    rows = connection.execute(latest_sessions_sql(table), (list(taxonomies), trade_date, trade_date)).fetchall()
    return {str(row["taxonomy_key"]): row["latest"] for row in rows}


def concept_flow_source(taxonomy_key: str) -> dict[str, Any]:
    """Which concept flow a scan served, and whether limit strength joins it."""
    if taxonomy_key == "ths_concept_flow":
        return {"taxonomy_key": taxonomy_key, "source": "tushare:moneyflow_cnt_ths (history, retired 2026-10-08)",
                "strength": {"status": "joined", "taxonomy_key": "ths_limit_strength"}}
    return {
        "taxonomy_key": taxonomy_key, "requested_taxonomy_key": "ths_concept_flow",
        "source": "eastmoney concept board flow, closing-window snapshot",
        "strength": {"status": "not_joined", "taxonomy_key": "fuyao_ths_concept_limit_strength",
                     "reason": "limit strength is keyed by THS concept codes; Eastmoney concept boards are not, "
                               "and boards are never matched by name"},
    }


def concept_candidate_source(taxonomy_key: str) -> dict[str, Any]:
    if taxonomy_key == "ths_concept_flow":
        return {"taxonomy_key": taxonomy_key, "source": "tushare:ths_member x limit_list_ths (history, retired 2026-10-08)",
                "matching_rule": "same-day THS concept member code equals THS limit-up-pool stock code"}
    return {"taxonomy_key": taxonomy_key, "requested_taxonomy_key": "ths_concept_flow",
            "source": "fuyao_ths:limit_up_pool x fuyao_ths_concept",
            "matching_rule": "same-session fuyao_ths_concept member code equals Fuyao limit-up-pool stock code",
            "board_context": "board_limit_up_count / board_strength_rank; no THS concept net flow exists"}


def concept_sector_signals(database: Any, trade_date: date | None, limit: int) -> dict[str, Any]:
    """Return a transparent persisted concept-flow scan without provider calls."""
    with database.transaction() as connection:
        latest = _latest_sessions(connection, "quant.sector_market_observations", CONCEPT_FLOW_TAXONOMIES, trade_date)
        taxonomy_key, selected_date = select_taxonomy(latest, CONCEPT_FLOW_TAXONOMIES)
        if selected_date is None:
            return {"trade_date": None, "items": [], "scoring": {"decision_eligible": False},
                    **concept_flow_source(taxonomy_key)}
        rows = connection.execute(
            CONCEPT_SIGNALS_SQL,
            (taxonomy_key, selected_date, STRENGTH_FOR_FLOW.get(taxonomy_key), selected_date, max(1, min(limit, 1000))),
        ).fetchall()
    return {**project_concept_sector_signals(rows, selected_date), **concept_flow_source(taxonomy_key)}


def project_concept_sector_signals(rows: list[Any], selected_date: date) -> dict[str, Any]:
    """Score already-read concept rows without database or provider access."""
    items: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        flow_score = round(float(item["flow_percentile"] or 0) * 100, 2)
        momentum_score = round(max(0.0, min(100.0, 50.0 + float(item["change_pct"] or 0) * 5.0)), 2)
        if item["up_nums"] is None:
            strength_score = None
            aggregate_score = round(flow_score * 0.65 + momentum_score * 0.35, 2)
        else:
            strength_score = round(min(100.0, float(item["up_nums"]) * 10.0 + float(item["streak_days"] or 0) * 3.0), 2)
            aggregate_score = round(flow_score * 0.50 + momentum_score * 0.30 + strength_score * 0.20, 2)
        item.update({"flow_score": flow_score, "momentum_score": momentum_score,
                     "strength_score": strength_score, "aggregate_score": aggregate_score})
        item.pop("raw", None)
        item.pop("strength_raw", None)
        items.append(item)
    items.sort(key=lambda item: float(item["aggregate_score"]), reverse=True)
    return {
        "trade_date": str(selected_date), "items": items,
        "scoring": {"decision_eligible": False, "purpose": "board_scan_only",
                    "weights": "flow 65% + momentum 35%; when limit-up strength exists: 50% + 30% + 20%"},
    }


def concept_limit_candidates(database: Any, trade_date: date | None, limit: int) -> dict[str, Any]:
    with database.transaction() as connection:
        latest = _latest_sessions(connection, "quant.sector_limit_candidates", CANDIDATE_TAXONOMIES, trade_date)
        taxonomy_key, selected_date = select_taxonomy(latest, CANDIDATE_TAXONOMIES)
        if selected_date is None:
            return {"trade_date": None, "items": [], "decision_eligible": False, **concept_candidate_source(taxonomy_key)}
        rows = connection.execute(
            CONCEPT_CANDIDATES_SQL,
            (FLOW_FOR_CANDIDATES.get(taxonomy_key), taxonomy_key, selected_date, max(1, min(limit, 200))),
        ).fetchall()
    return {"trade_date": str(selected_date), "items": rows, "decision_eligible": False,
            **concept_candidate_source(taxonomy_key)}


def sector_flows(database: Any, taxonomy_key: str, trade_date: date | None, limit: int) -> dict[str, Any]:
    with database.transaction() as connection:
        selected_date = trade_date or connection.execute(
            "SELECT max(trading_date) latest FROM quant.sector_market_observations WHERE taxonomy_key=%s", (taxonomy_key,)
        ).fetchone()["latest"]
        if selected_date is None:
            return {"taxonomy_key": taxonomy_key, "trade_date": None, "items": [], **superseded_notice(taxonomy_key)}
        rows = connection.execute(
            """SELECT o.taxonomy_key,o.sector_key,s.label,o.trading_date,o.close,o.change_pct,o.net_amount,o.net_buy_amount,o.net_sell_amount,
                      o.constituent_count,o.leading_symbol,o.leading_label,o.provider_key,o.available_at
                 FROM quant.sector_market_observations o JOIN quant.sectors s ON s.taxonomy_key=o.taxonomy_key AND s.sector_key=o.sector_key
                WHERE o.taxonomy_key=%s AND o.trading_date=%s
                ORDER BY o.net_amount DESC NULLS LAST,s.label LIMIT %s""",
            (taxonomy_key, selected_date, max(1, min(limit, 500))),
        ).fetchall()
    return {"taxonomy_key": taxonomy_key, "trade_date": str(selected_date), "items": rows,
            **superseded_notice(taxonomy_key)}


def market_sectors(database: Any, taxonomy_key: str, limit: int, offset: int) -> dict[str, Any]:
    bounded_limit, bounded_offset = max(1, min(limit, 1000)), max(0, offset)
    with database.transaction() as connection:
        rows = connection.execute(
            """SELECT s.taxonomy_key,s.sector_key,s.label,s.metadata,s.updated_at,count(m.symbol)::int active_members
                 FROM quant.sectors s LEFT JOIN quant.sector_membership_history m
                   ON m.taxonomy_key=s.taxonomy_key AND m.sector_key=s.sector_key AND m.effective_to IS NULL
                WHERE s.taxonomy_key=%s GROUP BY s.taxonomy_key,s.sector_key,s.label,s.metadata,s.updated_at
                ORDER BY s.label LIMIT %s OFFSET %s""",
            (taxonomy_key, bounded_limit, bounded_offset),
        ).fetchall()
        total = connection.execute("SELECT count(*)::int total FROM quant.sectors WHERE taxonomy_key=%s", (taxonomy_key,)).fetchone()["total"]
    return {"taxonomy_key": taxonomy_key, "items": rows, "limit": bounded_limit, "offset": bounded_offset, "total": total,
            "next_offset": bounded_offset + len(rows) if bounded_offset + len(rows) < total else None}


def sector_members(database: Any, sector_key: str, taxonomy_key: str, limit: int, offset: int) -> dict[str, Any]:
    bounded_limit, bounded_offset = max(1, min(limit, 1000)), max(0, offset)
    with database.transaction() as connection:
        rows = connection.execute(
            """SELECT m.symbol,i.name,i.industry,m.effective_from,m.effective_to,m.provider_key,m.available_at
                 FROM quant.sector_membership_history m JOIN quant.instruments i ON i.symbol=m.symbol
                WHERE m.taxonomy_key=%s AND m.sector_key=%s AND m.effective_to IS NULL
                ORDER BY m.symbol LIMIT %s OFFSET %s""",
            (taxonomy_key, sector_key, bounded_limit, bounded_offset),
        ).fetchall()
        total = connection.execute(
            "SELECT count(*)::int total FROM quant.sector_membership_history WHERE taxonomy_key=%s AND sector_key=%s AND effective_to IS NULL",
            (taxonomy_key, sector_key),
        ).fetchone()["total"]
    return {"taxonomy_key": taxonomy_key, "sector_key": sector_key, "items": rows,
            "limit": bounded_limit, "offset": bounded_offset, "total": total,
            "next_offset": bounded_offset + len(rows) if bounded_offset + len(rows) < total else None}


__all__ = [
    "ACTIVE_MAPPING_SQL", "CANDIDATE_TAXONOMIES", "CONCEPT_CANDIDATES_SQL", "CONCEPT_FLOW_TAXONOMIES",
    "CONCEPT_SIGNALS_SQL", "FLOW_FOR_CANDIDATES", "MEMBERSHIP_PROGRESS_SQL", "MEMBERSHIP_TAXONOMIES",
    "STRENGTH_FOR_FLOW", "SUPERSEDED_TAXONOMIES", "SYNC_STATES_SQL", "concept_candidate_source",
    "concept_flow_source", "concept_limit_candidates", "concept_member_backfill_status", "concept_sector_signals",
    "latest_sessions_sql", "market_sectors", "project_concept_member_backfill_status",
    "project_concept_sector_signals", "project_membership_refresh_status", "sector_flows", "sector_members",
    "select_taxonomy", "superseded_notice",
]
