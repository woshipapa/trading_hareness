"""Measure the native async history estimate used by research overview.

Run from the quant image with the local compose PostgreSQL service. This probe
only reads persisted research evidence; it does not contact a provider or
write a row.
"""

from __future__ import annotations

import asyncio
import statistics
import time

from app.async_research_readiness_repository import historical_estimate
from app.database import AsyncDatabase, Database
from app.research_capacity import historical_estimate_from_db
from app.request_models import HistoricalCoverageEstimateRequest


async def main() -> None:
    request = HistoricalCoverageEstimateRequest(years=3, include_minute=False)
    database = Database()
    async_database = AsyncDatabase(database)
    samples: list[float] = []
    payload = None
    try:
        for _ in range(3):
            started = time.perf_counter()
            payload = await historical_estimate(async_database, request)
            samples.append((time.perf_counter() - started) * 1000)
        sync_payload = historical_estimate_from_db(database, request)
    finally:
        await async_database.close()
        database.close()
    assert payload is not None
    print({
        "parallel_read_count": 7,
        "elapsed_ms": [round(value, 3) for value in samples],
        "median_ms": round(statistics.median(samples), 3),
        "coverage_equal_to_sync": payload["current_coverage"] == sync_payload["current_coverage"],
        "dataset_rows_equal_to_sync": [
            (item["dataset"], item["rows"]) for item in payload["datasets"]
        ] == [
            (item["dataset"], item["rows"]) for item in sync_payload["datasets"]
        ],
        "coverage": payload["current_coverage"],
        "scope": "local compose PostgreSQL; no owner tunnel/provider access",
    })


if __name__ == "__main__":
    asyncio.run(main())
