"""Native-async projections for persisted sector, concept and member evidence.

The SQL and the projections are the synchronous read model's
(``sector_read_model``), so both paths serve the same taxonomy for the same
request; only the driver differs.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Sequence

from .sector_read_model import (
    ACTIVE_MAPPING_SQL, CANDIDATE_TAXONOMIES, CONCEPT_CANDIDATES_SQL, CONCEPT_FLOW_TAXONOMIES, CONCEPT_SIGNALS_SQL,
    FLOW_FOR_CANDIDATES, MEMBERSHIP_PROGRESS_SQL, MEMBERSHIP_TAXONOMIES, STRENGTH_FOR_FLOW, SYNC_STATES_SQL,
    concept_candidate_source, concept_flow_source, latest_sessions_sql, project_concept_sector_signals,
    project_membership_refresh_status, select_taxonomy, superseded_notice,
)


async def _latest_date(connection: Any, table: str, taxonomy_key: str) -> date | None:
    result = await connection.execute(
        f"SELECT max(trading_date) latest FROM {table} WHERE taxonomy_key=%s", (taxonomy_key,),
    )
    row = await result.fetchone()
    return row["latest"] if row else None


async def _latest_sessions(connection: Any, table: str, taxonomies: Sequence[str],
                           trade_date: date | None) -> dict[str, Any]:
    result = await connection.execute(latest_sessions_sql(table), (list(taxonomies), trade_date, trade_date))
    return {str(row["taxonomy_key"]): row["latest"] for row in await result.fetchall()}


async def concept_member_backfill_status(
    async_database: Any, trade_date: date | None, *, automatic_enabled: bool, batch_size: int,
) -> dict[str, Any]:
    concept = MEMBERSHIP_TAXONOMIES[0]
    async with async_database.transaction() as connection:
        selected_date = trade_date
        if selected_date is None:
            latest = await connection.execute(
                "SELECT max(trading_date) latest FROM quant.sector_member_sync_state WHERE taxonomy_key=ANY(%s)",
                (list(MEMBERSHIP_TAXONOMIES),),
            )
            row = await latest.fetchone()
            selected_date = row["latest"] if row else None
        if selected_date is None:
            return {"trade_date": None, "total_concepts": 0, "mapped_concepts": 0, "states": [],
                    "taxonomy_key": concept, "taxonomy_keys": list(MEMBERSHIP_TAXONOMIES),
                    "requested_taxonomy_key": "ths_concept_flow", "source": "fuyao_ths",
                    "notice": "尚未刷新 Fuyao 同花顺板块成分。"}
        progress_result = await connection.execute(
            MEMBERSHIP_PROGRESS_SQL, (selected_date, list(MEMBERSHIP_TAXONOMIES), selected_date.isoformat()),
        )
        active_result = await connection.execute(
            ACTIVE_MAPPING_SQL, (concept, selected_date, concept, selected_date.isoformat()),
        )
        states_result = await connection.execute(SYNC_STATES_SQL, (concept, selected_date))
        progress = [dict(row) for row in await progress_result.fetchall()]
        active_mapping = dict(await active_result.fetchone() or {})
        states = [dict(row) for row in await states_result.fetchall()]
    return project_membership_refresh_status(
        selected_date, progress, active_mapping, states,
        automatic_enabled=automatic_enabled, batch_size=batch_size,
    )


async def concept_sector_signals(async_database: Any, trade_date: date | None, limit: int) -> dict[str, Any]:
    async with async_database.transaction() as connection:
        latest = await _latest_sessions(connection, "quant.sector_market_observations", CONCEPT_FLOW_TAXONOMIES, trade_date)
        taxonomy_key, selected_date = select_taxonomy(latest, CONCEPT_FLOW_TAXONOMIES)
        if selected_date is None:
            return {"trade_date": None, "items": [], "scoring": {"decision_eligible": False},
                    **concept_flow_source(taxonomy_key)}
        result = await connection.execute(
            CONCEPT_SIGNALS_SQL,
            (taxonomy_key, selected_date, STRENGTH_FOR_FLOW.get(taxonomy_key), selected_date,
             max(1, min(int(limit), 1000))),
        )
        rows = [dict(row) for row in await result.fetchall()]
    return {**project_concept_sector_signals(rows, selected_date), **concept_flow_source(taxonomy_key)}


async def concept_limit_candidates(async_database: Any, trade_date: date | None, limit: int) -> dict[str, Any]:
    async with async_database.transaction() as connection:
        latest = await _latest_sessions(connection, "quant.sector_limit_candidates", CANDIDATE_TAXONOMIES, trade_date)
        taxonomy_key, selected_date = select_taxonomy(latest, CANDIDATE_TAXONOMIES)
        if selected_date is None:
            return {"trade_date": None, "items": [], "decision_eligible": False, **concept_candidate_source(taxonomy_key)}
        result = await connection.execute(
            CONCEPT_CANDIDATES_SQL,
            (FLOW_FOR_CANDIDATES.get(taxonomy_key), taxonomy_key, selected_date, max(1, min(int(limit), 200))),
        )
        rows = [dict(row) for row in await result.fetchall()]
    return {"trade_date": str(selected_date), "items": rows, "decision_eligible": False,
            **concept_candidate_source(taxonomy_key)}


async def sector_flows(async_database: Any, taxonomy_key: str, trade_date: date | None, limit: int) -> dict[str, Any]:
    async with async_database.transaction() as connection:
        selected_date = trade_date or await _latest_date(connection, "quant.sector_market_observations", taxonomy_key)
        if selected_date is None:
            return {"taxonomy_key": taxonomy_key, "trade_date": None, "items": [], **superseded_notice(taxonomy_key)}
        result = await connection.execute(
            """SELECT o.taxonomy_key,o.sector_key,s.label,o.trading_date,o.close,o.change_pct,o.net_amount,o.net_buy_amount,o.net_sell_amount,
                      o.constituent_count,o.leading_symbol,o.leading_label,o.provider_key,o.available_at
                 FROM quant.sector_market_observations o JOIN quant.sectors s ON s.taxonomy_key=o.taxonomy_key AND s.sector_key=o.sector_key
                WHERE o.taxonomy_key=%s AND o.trading_date=%s
                ORDER BY o.net_amount DESC NULLS LAST,s.label LIMIT %s""",
            (taxonomy_key, selected_date, max(1, min(int(limit), 500))),
        )
        rows = [dict(row) for row in await result.fetchall()]
    return {"taxonomy_key": taxonomy_key, "trade_date": str(selected_date), "items": rows,
            **superseded_notice(taxonomy_key)}


async def market_sectors(async_database: Any, taxonomy_key: str, limit: int, offset: int) -> dict[str, Any]:
    bounded_limit, bounded_offset = max(1, min(int(limit), 1000)), max(0, int(offset))
    async with async_database.transaction() as connection:
        rows_result = await connection.execute(
            """SELECT s.taxonomy_key,s.sector_key,s.label,s.metadata,s.updated_at,count(m.symbol)::int active_members
                 FROM quant.sectors s LEFT JOIN quant.sector_membership_history m
                   ON m.taxonomy_key=s.taxonomy_key AND m.sector_key=s.sector_key AND m.effective_to IS NULL
                WHERE s.taxonomy_key=%s GROUP BY s.taxonomy_key,s.sector_key,s.label,s.metadata,s.updated_at
                ORDER BY s.label LIMIT %s OFFSET %s""",
            (taxonomy_key, bounded_limit, bounded_offset),
        )
        total_result = await connection.execute("SELECT count(*)::int total FROM quant.sectors WHERE taxonomy_key=%s", (taxonomy_key,))
        rows = [dict(row) for row in await rows_result.fetchall()]
        total = int((await total_result.fetchone())["total"] or 0)
    return {"taxonomy_key": taxonomy_key, "items": rows, "limit": bounded_limit, "offset": bounded_offset, "total": total,
            "next_offset": bounded_offset + len(rows) if bounded_offset + len(rows) < total else None}


async def sector_members(async_database: Any, sector_key: str, taxonomy_key: str, limit: int, offset: int) -> dict[str, Any]:
    bounded_limit, bounded_offset = max(1, min(int(limit), 1000)), max(0, int(offset))
    async with async_database.transaction() as connection:
        rows_result = await connection.execute(
            """SELECT m.symbol,i.name,i.industry,m.effective_from,m.effective_to,m.provider_key,m.available_at
                 FROM quant.sector_membership_history m JOIN quant.instruments i ON i.symbol=m.symbol
                WHERE m.taxonomy_key=%s AND m.sector_key=%s AND m.effective_to IS NULL
                ORDER BY m.symbol LIMIT %s OFFSET %s""",
            (taxonomy_key, sector_key, bounded_limit, bounded_offset),
        )
        total_result = await connection.execute(
            "SELECT count(*)::int total FROM quant.sector_membership_history WHERE taxonomy_key=%s AND sector_key=%s AND effective_to IS NULL",
            (taxonomy_key, sector_key),
        )
        rows = [dict(row) for row in await rows_result.fetchall()]
        total = int((await total_result.fetchone())["total"] or 0)
    return {"taxonomy_key": taxonomy_key, "sector_key": sector_key, "items": rows,
            "limit": bounded_limit, "offset": bounded_offset, "total": total,
            "next_offset": bounded_offset + len(rows) if bounded_offset + len(rows) < total else None}


__all__ = [
    "concept_limit_candidates", "concept_member_backfill_status", "concept_sector_signals",
    "market_sectors", "sector_flows", "sector_members",
]
