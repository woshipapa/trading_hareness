"""Compare the coalesced market-flow read with its five-statement predecessor.

Run from the quant image with the local compose PostgreSQL service. This probe
only reads persisted research evidence; it does not contact a provider or
write a row.
"""

from __future__ import annotations

import asyncio
from datetime import date
import statistics
import time

from app.async_market_flow_read_repository import market_flow_features
from app.database import AsyncDatabase
from app.market_flow_read_model import SECTOR_DAILY_SQL, SECTOR_OUTCOME_SQL, project_market_flow_features


ITEMS_SQL = """SELECT feature_key,exchange_date,cadence,observed_at,source_snapshot_minute,status,market_state,
                      concept_count,concept_positive_ratio,concept_median_flow,concept_mean_change_pct,
                      five_minute_positive_ratio_delta,session_positive_ratio_delta,afternoon_repair_strength,
                      market_amount,market_volume,amount_change_pct,volume_change_pct,advancer_ratio,
                      features,quality_flags,updated_at
                 FROM quant.market_flow_feature_snapshots
                WHERE exchange_date=%s ORDER BY observed_at LIMIT %s"""
DAILY_SQL = """SELECT DISTINCT ON(exchange_date) feature_key,exchange_date,cadence,observed_at,status,market_state,
                      concept_count,concept_positive_ratio,market_amount,market_volume,amount_change_pct,
                      volume_change_pct,advancer_ratio,features,quality_flags
                 FROM quant.market_flow_feature_snapshots
                WHERE cadence IN ('close','midday')
                ORDER BY exchange_date DESC,
                         CASE cadence WHEN 'close' THEN 0 ELSE 1 END,observed_at DESC LIMIT 20"""
READINESS_SQL = """SELECT (SELECT count(DISTINCT trading_date) FROM quant.sector_flow_daily_features) AS trading_days,
                      count(DISTINCT (sector_key,signal_date)) FILTER (WHERE status='matured') AS matured_events
                 FROM quant.sector_flow_daily_outcomes"""


async def legacy_read(database: AsyncDatabase, selected_date: date) -> dict[str, object]:
    async with database.transaction() as connection:
        rows = await (await connection.execute(ITEMS_SQL, (selected_date, 720))).fetchall()
        daily_rows = await (await connection.execute(DAILY_SQL)).fetchall()
        sector_rows = await (await connection.execute(SECTOR_DAILY_SQL, (selected_date, selected_date))).fetchall()
        outcome_rows = await (await connection.execute(SECTOR_OUTCOME_SQL)).fetchall()
        readiness = await (await connection.execute(READINESS_SQL)).fetchone()
    return project_market_flow_features(rows, daily_rows, sector_rows, outcome_rows, readiness, selected_date)


async def legacy_statements(database: AsyncDatabase, selected_date: date) -> None:
    """Execute the legacy shape without projecting it, for timing only."""
    async with database.transaction() as connection:
        for sql, params, fetch_one in (
            (ITEMS_SQL, (selected_date, 720), False),
            (DAILY_SQL, (), False),
            (SECTOR_DAILY_SQL, (selected_date, selected_date), False),
            (SECTOR_OUTCOME_SQL, (), False),
            (READINESS_SQL, (), True),
        ):
            result = await connection.execute(sql, params)
            await (result.fetchone() if fetch_one else result.fetchall())


async def main() -> None:
    database = AsyncDatabase()
    await database.open()
    selected_date = date(2026, 10, 15)
    snapshot_samples: list[float] = []
    legacy_samples: list[float] = []
    for _ in range(5):
        started = time.perf_counter()
        payload = await market_flow_features(database, selected_date, limit=720)
        snapshot_samples.append((time.perf_counter() - started) * 1000)
        started = time.perf_counter()
        await legacy_statements(database, selected_date)
        legacy_samples.append((time.perf_counter() - started) * 1000)
    legacy_payload = await legacy_read(database, selected_date)
    print({
        "statement_count": 1,
        "legacy_statement_count": 5,
        "snapshot_elapsed_ms": [round(item, 3) for item in snapshot_samples],
        "legacy_elapsed_ms": [round(item, 3) for item in legacy_samples],
        "snapshot_median_ms": round(statistics.median(snapshot_samples), 3),
        "legacy_median_ms": round(statistics.median(legacy_samples), 3),
        "items": len(payload["items"]),
        "daily": len(payload["daily"]),
        "sector_daily": len(payload["sector_daily"]),
        "sector_outcome_summary": len(payload["sector_outcome_summary"]),
        "semantic_equivalence": {
            "top_keys": set(payload) == set(legacy_payload),
            "counts": all(len(payload[key]) == len(legacy_payload[key]) for key in
                           ("items", "daily", "sector_daily", "sector_outcome_summary")),
            "state_counts": payload["state_counts"] == legacy_payload["state_counts"],
            "research_gate": payload["research_gate"] == legacy_payload["research_gate"],
            "sector_source": payload["sector_daily_source"] == legacy_payload["sector_daily_source"],
        },
        "scope": "local compose PostgreSQL; no owner tunnel/provider access",
    })
    await database.close()


if __name__ == "__main__":
    asyncio.run(main())
