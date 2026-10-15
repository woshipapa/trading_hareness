"""Measure the read-only strategy-health snapshot on local PostgreSQL."""

from __future__ import annotations

import json
import os
import time

import psycopg
from psycopg.rows import dict_row

from app.strategy_health_read_model import STRATEGY_HEALTH_SQL


def main() -> None:
    samples: list[float] = []
    with psycopg.connect(
        host=os.getenv("PGHOST", "postgres"), port=os.getenv("PGPORT", "5432"),
        dbname=os.getenv("PGDATABASE", "n8n"), user=os.getenv("PGUSER", "n8n"),
        password=os.getenv("PGPASSWORD", ""), row_factory=dict_row,
    ) as database:
        for _ in range(5):
            started = time.perf_counter()
            with database.cursor() as cursor:
                cursor.execute(STRATEGY_HEALTH_SQL)
                cursor.fetchone()
            samples.append(round((time.perf_counter() - started) * 1000, 3))
    print(json.dumps({
        "statement_count": 1,
        "elapsed_ms": samples,
        "median_ms": sorted(samples)[len(samples) // 2],
        "scope": "local compose PostgreSQL; no owner tunnel/provider access",
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
