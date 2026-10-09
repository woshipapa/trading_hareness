"""Native-async exact relation evidence for live limit-up linkage research.

Concept membership is the Fuyao-refreshed 同花顺 list (``fuyao_ths_concept``).
The Tushare-era ``ths_concept_flow`` rows stopped refreshing on 2026-10-08 and
their open intervals never close, so they are read only for a symbol Fuyao has
no current membership for; each row names the taxonomies it came from.  Both
use the same ``NNNNNN.TI`` codes, so one concept is one key across them.
"""

from __future__ import annotations

from datetime import date
from typing import Any

LIVE_TAXONOMY = "fuyao_ths_concept"
FROZEN_TAXONOMY = "ths_concept_flow"


async def relations(async_database: Any, trade_date: date) -> list[dict[str, Any]]:
    """Return non-anchor peers sharing active, bounded THS concepts exactly."""
    async with async_database.transaction() as connection:
        result = await connection.execute(
            """WITH live AS (
                   SELECT symbol,sector_key,taxonomy_key FROM quant.sector_membership_history
                    WHERE taxonomy_key=%s AND effective_to IS NULL
                 ), membership AS (
                   SELECT symbol,sector_key,taxonomy_key FROM live
                   UNION ALL
                   SELECT frozen.symbol,frozen.sector_key,frozen.taxonomy_key
                     FROM quant.sector_membership_history frozen
                    WHERE frozen.taxonomy_key=%s AND frozen.effective_to IS NULL
                      AND NOT EXISTS (SELECT 1 FROM live WHERE live.symbol=frozen.symbol)
                 ), anchors AS (
                   SELECT DISTINCT event.symbol,coalesce(instrument.name,event.symbol) AS name
                     FROM quant.market_events event
                LEFT JOIN quant.instruments instrument ON instrument.symbol=event.symbol
                    WHERE event.event_type='limit_up_pool'
                      AND (event.occurred_at AT TIME ZONE 'Asia/Shanghai')::date=%s
                 ), eligible_concepts AS (
                   SELECT sector_key FROM membership
                    GROUP BY sector_key
                   HAVING count(DISTINCT symbol) BETWEEN 2 AND 200
                 ), shared AS (
                   SELECT candidate.symbol,anchor.symbol AS leader_symbol,anchor.name AS leader_name,
                          leader.sector_key,coalesce(live_sector.label,frozen_sector.label) AS label,
                          leader.taxonomy_key AS leader_taxonomy,candidate.taxonomy_key AS candidate_taxonomy
                     FROM anchors anchor
                     JOIN membership leader ON leader.symbol=anchor.symbol
                     JOIN eligible_concepts eligible ON eligible.sector_key=leader.sector_key
                     JOIN membership candidate
                       ON candidate.sector_key=leader.sector_key AND candidate.symbol<>anchor.symbol
                      AND candidate.symbol NOT IN (SELECT symbol FROM anchors)
                LEFT JOIN quant.sectors live_sector
                       ON live_sector.taxonomy_key=%s AND live_sector.sector_key=leader.sector_key
                LEFT JOIN quant.sectors frozen_sector
                       ON frozen_sector.taxonomy_key=%s AND frozen_sector.sector_key=leader.sector_key
                    WHERE coalesce(live_sector.label,frozen_sector.label) IS NOT NULL
                 )
                 SELECT symbol,array_agg(DISTINCT sector_key) AS concept_keys,array_agg(DISTINCT label) AS concept_labels,
                        array_agg(DISTINCT leader_symbol) AS leader_symbols,array_agg(DISTINCT leader_name) AS leader_names,
                        array_agg(DISTINCT leader_taxonomy) AS leader_taxonomies,
                        array_agg(DISTINCT candidate_taxonomy) AS candidate_taxonomies
                   FROM shared GROUP BY symbol""",
            (LIVE_TAXONOMY, FROZEN_TAXONOMY, trade_date, LIVE_TAXONOMY, FROZEN_TAXONOMY),
        )
        rows = await result.fetchall()
    projected = []
    for raw in rows:
        row = dict(raw)
        taxonomies = sorted({*(row.pop("leader_taxonomies", None) or []), *(row.pop("candidate_taxonomies", None) or [])})
        projected.append({**row, "shared_concepts": len(row["concept_keys"] or []),
                          "membership_taxonomies": taxonomies, "frozen_membership": FROZEN_TAXONOMY in taxonomies})
    return projected


__all__ = ["FROZEN_TAXONOMY", "LIVE_TAXONOMY", "relations"]
