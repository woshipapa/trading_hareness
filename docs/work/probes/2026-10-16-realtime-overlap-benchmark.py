"""Deterministic local timing probe for the two scan orchestration seams."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import random
from time import perf_counter
from types import SimpleNamespace

from app.intraday_watchlist_scan_service import (
    _capture_with_memberships,
    _surge_and_board_evidence,
)


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * fraction))))
    return round(ordered[index], 3)


async def main() -> None:
    rng = random.Random(20261016)
    capture_speedups: list[float] = []
    enrichment_speedups: list[float] = []
    observed_at = datetime(2026, 10, 16, 1, 55, tzinfo=timezone.utc)

    for _ in range(200):
        membership_delay = 0.004 + rng.random() * 0.012
        quote_delay = 0.006 + rng.random() * 0.016
        minute_delay = 0.005 + rng.random() * 0.014
        board_delay = 0.003 + rng.random() * 0.010

        async def memberships(_symbols, _observed_at, delay=membership_delay):
            await asyncio.sleep(delay)
            return []

        async def quotes(_symbols, _observed_at, _slo, delay=quote_delay):
            await asyncio.sleep(delay)
            return SimpleNamespace()

        dependencies = SimpleNamespace(
            now_utc=lambda: observed_at,
            load_exact_memberships=memberships,
            capture_quotes=quotes,
            surge_context=None,
            board_cache_evidence=None,
        )
        started = perf_counter()
        await _capture_with_memberships(dependencies, ["000001.SZ"], observed_at, 20.0)
        concurrent_ms = (perf_counter() - started) * 1000
        started = perf_counter()
        await memberships(["000001.SZ"], observed_at)
        await quotes(["000001.SZ"], observed_at, 20.0)
        serial_ms = (perf_counter() - started) * 1000
        capture_speedups.append(serial_ms / concurrent_ms)

        async def minute(*_args, delay=minute_delay, **_kwargs):
            await asyncio.sleep(delay)
            return {}, {}

        async def board(_observed_at, delay=board_delay):
            await asyncio.sleep(delay)
            return {}

        dependencies.surge_context = minute
        dependencies.board_cache_evidence = board
        started = perf_counter()
        await _surge_and_board_evidence(dependencies, [], {}, [], observed_at)
        concurrent_ms = (perf_counter() - started) * 1000
        started = perf_counter()
        await minute([], mapped_peers={}, priority_symbols=[])
        await board(observed_at)
        serial_ms = (perf_counter() - started) * 1000
        enrichment_speedups.append(serial_ms / concurrent_ms)

    print(json.dumps({
        "runs": 200,
        "clock": "perf_counter",
        "capture_membership_speedup": {
            "p50_x": percentile(capture_speedups, 0.50),
            "p95_x": percentile(capture_speedups, 0.95),
        },
        "minute_board_speedup": {
            "p50_x": percentile(enrichment_speedups, 0.50),
            "p95_x": percentile(enrichment_speedups, 0.95),
        },
        "scope": "synthetic bounded I/O sleeps; validates orchestration overlap, not provider throughput",
    }, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
