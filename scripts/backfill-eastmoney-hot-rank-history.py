#!/usr/bin/env python3
"""Backfill about a year of Eastmoney daily popularity rank per stock.

The THS hot list has no history; Eastmoney's ``getHisList`` does (~366 days
per symbol), so an attention series can be seeded instead of only captured
forward.  Rows are timed observations: ``effective_at`` is each session's
close, ``available_at`` the backfill time -- a replay never sees a rank
before it was actually fetched.

Default symbols are the enabled watchlist; ``--symbols`` overrides::

    docker exec trading-hareness-peer-quant-research-scheduler-1 python /tmp/backfill.py --symbols 000001.SZ,600519.SH
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime, timezone

sys.path.insert(0, "/app")

from app.datasources.sources.eastmoney_hot_rank import PROVIDER_KEY, fetch_rank_history  # noqa: E402


async def run(symbols: list[str], delay: float, dry_run: bool) -> int:
    import app.main as service  # noqa: PLC0415

    total = 0
    for index, symbol in enumerate(symbols, start=1):
        try:
            rows = await fetch_rank_history(symbol)
        except Exception as error:  # noqa: BLE001
            print(f"{symbol}: failed {type(error).__name__}: {str(error)[:120]}")
            continue
        now = datetime.now(timezone.utc).isoformat()
        observations = [{**row, "ts_code": symbol, "available_at": now} for row in rows]
        stored = len(observations) if dry_run else service.persist_timed_observations(
            PROVIDER_KEY, "hot_rank_popularity_history", observations)
        total += stored
        print(f"{index}/{len(symbols)} {symbol}: {len(rows)} days, stored {stored}")
        await asyncio.sleep(delay)
    return total


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", default="")
    parser.add_argument("--delay-seconds", type=float, default=0.3)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    symbols = [item.strip().upper() for item in args.symbols.split(",") if item.strip()]
    if not symbols:
        import app.main as service  # noqa: PLC0415
        from app.datasources.storage import enabled_watch_symbols  # noqa: PLC0415
        symbols = enabled_watch_symbols(service.db, 300)
    if not symbols:
        print("no symbols: pass --symbols or enable watchlist names")
        return 1
    return 0 if asyncio.run(run(symbols, max(0.0, args.delay_seconds), args.dry_run)) else 1


if __name__ == "__main__":
    raise SystemExit(main())
