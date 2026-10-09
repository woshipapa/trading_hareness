"""Native-async projections for persisted sector, concept and member evidence."""

from __future__ import annotations

from datetime import date
from typing import Any

from .sector_read_model import (
    ACTIVE_MAPPING_SQL, MEMBERSHIP_PROGRESS_SQL, MEMBERSHIP_TAXONOMIES, SYNC_STATES_SQL, project_concept_sector_signals,
    project_membership_refresh_status,
)


async def _latest_date(connection: Any, table: str, taxonomy_key: str) -> date | None:
    result = await connection.execute(
        f"SELECT max(trading_date) latest FROM {table} WHERE taxonomy_key=%s", (taxonomy_key,),
    )
    row = await result.fetchone()
    return row["latest"] if row else None


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
        selected_date = trade_date or await _latest_date(connection, "quant.sector_market_observations", "ths_concept_flow")
        if selected_date is None:
            return {"trade_date": None, "items": [], "scoring": {"decision_eligible": False}}
        result = await connection.execute(
            """WITH concept AS (
                   SELECT o.sector_key,s.label,o.close,o.change_pct,o.net_amount,o.net_buy_amount,o.net_sell_amount,
                          o.constituent_count,o.leading_label,o.provider_key,o.available_at,o.raw,
                          percent_rank() OVER (ORDER BY o.net_amount NULLS FIRST) AS flow_percentile
                     FROM quant.sector_market_observations o
                     JOIN quant.sectors s ON s.taxonomy_key=o.taxonomy_key AND s.sector_key=o.sector_key
                    WHERE o.taxonomy_key='ths_concept_flow' AND o.trading_date=%s
                 )
                 SELECT c.*,ls.provider_key strength_provider,ls.raw strength_raw,
                        nullif(ls.raw->>'up_nums','')::numeric up_nums,
                        nullif(ls.raw->>'cons_nums','')::numeric strength_constituents,
                        nullif(ls.raw->>'days','')::numeric streak_days
                   FROM concept c
              LEFT JOIN quant.sector_market_observations ls
                     ON ls.taxonomy_key='ths_limit_strength' AND ls.trading_date=%s AND ls.sector_key=c.sector_key
                  ORDER BY c.net_amount DESC NULLS LAST,c.label LIMIT %s""",
            (selected_date, selected_date, max(1, min(int(limit), 1000))),
        )
        rows = [dict(row) for row in await result.fetchall()]
    return project_concept_sector_signals(rows, selected_date)


async def concept_limit_candidates(async_database: Any, trade_date: date | None, limit: int) -> dict[str, Any]:
    async with async_database.transaction() as connection:
        selected_date = trade_date or await _latest_date(connection, "quant.sector_limit_candidates", "ths_concept_flow")
        if selected_date is None:
            return {"trade_date": None, "items": [], "decision_eligible": False}
        result = await connection.execute(
            """SELECT c.sector_key,s.label concept_label,c.symbol,c.name,c.limit_tag,c.limit_type,c.pct_change,c.price,c.limit_amount,
                      c.turnover_rate,c.open_num,c.status,c.description,c.provider_key,c.available_at,
                      coalesce(c.raw->>'membership_fetch_status','unknown') membership_status,
                      flow.net_amount board_net_amount,flow.change_pct board_change_pct,flow.leading_label board_leading_label
                 FROM quant.sector_limit_candidates c
                 JOIN quant.sectors s ON s.taxonomy_key=c.taxonomy_key AND s.sector_key=c.sector_key
            LEFT JOIN quant.sector_market_observations flow ON flow.taxonomy_key='ths_concept_flow' AND flow.sector_key=c.sector_key
                      AND flow.trading_date=c.trading_date
                WHERE c.taxonomy_key='ths_concept_flow' AND c.trading_date=%s
                ORDER BY flow.net_amount DESC NULLS LAST,c.limit_amount DESC NULLS LAST,c.symbol LIMIT %s""",
            (selected_date, max(1, min(int(limit), 200))),
        )
        rows = [dict(row) for row in await result.fetchall()]
    return {"trade_date": str(selected_date), "items": rows, "decision_eligible": False,
            "matching_rule": "same-day THS concept member code equals THS limit-up-pool stock code"}


async def sector_flows(async_database: Any, taxonomy_key: str, trade_date: date | None, limit: int) -> dict[str, Any]:
    async with async_database.transaction() as connection:
        selected_date = trade_date or await _latest_date(connection, "quant.sector_market_observations", taxonomy_key)
        if selected_date is None:
            return {"taxonomy_key": taxonomy_key, "trade_date": None, "items": []}
        result = await connection.execute(
            """SELECT o.taxonomy_key,o.sector_key,s.label,o.trading_date,o.close,o.change_pct,o.net_amount,o.net_buy_amount,o.net_sell_amount,
                      o.constituent_count,o.leading_symbol,o.leading_label,o.provider_key,o.available_at
                 FROM quant.sector_market_observations o JOIN quant.sectors s ON s.taxonomy_key=o.taxonomy_key AND s.sector_key=o.sector_key
                WHERE o.taxonomy_key=%s AND o.trading_date=%s
                ORDER BY o.net_amount DESC NULLS LAST,s.label LIMIT %s""",
            (taxonomy_key, selected_date, max(1, min(int(limit), 500))),
        )
        rows = [dict(row) for row in await result.fetchall()]
    return {"taxonomy_key": taxonomy_key, "trade_date": str(selected_date), "items": rows}


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
