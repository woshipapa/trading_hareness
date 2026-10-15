"""Measure the post-close scheduler's audited overlap with synthetic I/O.

This probe exercises the real orchestration function with local sleeps only;
it does not contact a provider or a database and must not be read as an owner
throughput measurement.
"""

from __future__ import annotations

import asyncio
import json
import statistics
import time
from datetime import date

from app.post_close_refresh import run_refresh
from app.post_close_refresh_service import POST_CLOSE_PARALLEL_GROUPS


DELAYS = {
    "stale_fetch_runs": 0.05,
    "trade_calendar": 0.08,
    "analyst_text": 0.06,
    "analyst_outcomes": 0.10,
    "analyst_intraday_outcomes": 0.07,
    "market_temperature": 0.06,
    "market_temperature_intraday": 0.06,
    "broad_etf_flow": 0.08,
    "market_timing": 0.05,
}
STAGES = tuple(DELAYS)


async def measure(parallel_groups: tuple[tuple[str, ...], ...]) -> tuple[float, int]:
    active = 0
    peak = 0

    async def action(name: str) -> dict[str, str]:
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(DELAYS[name])
        active -= 1
        return {"status": "completed"}

    async def run_database(function, *args, **_kwargs):
        result = function(*args)
        return await result if hasattr(result, "__await__") else result

    actions = {name: (lambda name=name: action(name)) for name in STAGES}
    started = time.perf_counter()
    result = await run_refresh(
        object(), db=object(), lease_key="probe", lease_seconds=lambda: 60,
        run_database_blocking=run_database, acquire_lease=lambda *_: True,
        renew_lease=lambda *_: True, release_lease=lambda *_: None,
        actions=actions, stage_order=STAGES, trade_date=date(2026, 10, 16),
        safe_error_detail=lambda value, _limit: value, json_safe=lambda value: value,
        parallel_groups=parallel_groups,
    )
    assert result["status"] == "completed"
    return (time.perf_counter() - started) * 1000, peak


async def main() -> None:
    baseline = [await measure(()) for _ in range(20)]
    overlap = [await measure(POST_CLOSE_PARALLEL_GROUPS) for _ in range(20)]
    print(json.dumps({
        "scope": "local synthetic orchestration only; no owner/provider/database access",
        "baseline_ms": [round(item[0], 3) for item in baseline],
        "overlap_ms": [round(item[0], 3) for item in overlap],
        "baseline_median_ms": round(statistics.median(item[0] for item in baseline), 3),
        "overlap_median_ms": round(statistics.median(item[0] for item in overlap), 3),
        "baseline_peak": max(item[1] for item in baseline),
        "overlap_peak": max(item[1] for item in overlap),
        "groups": POST_CLOSE_PARALLEL_GROUPS,
    }, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
