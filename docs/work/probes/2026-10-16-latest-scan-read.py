"""Measure the latest-scan signal projection before and after its local index."""

from __future__ import annotations

import json
import os
import time

import psycopg


SIGNAL_READ_SQL = """
    SELECT *
      FROM quant.intraday_signal_events
     WHERE scan_id=(
        SELECT scan_id FROM quant.intraday_scan_runs
         ORDER BY observed_at DESC LIMIT 1
     )
     ORDER BY severity DESC,created_at
     LIMIT 100
"""
TEMP_INDEX_SQL = """
    CREATE INDEX IF NOT EXISTS tmp_scan_signal_read_idx
        ON quant.intraday_signal_events(scan_id, severity DESC, created_at)
"""


def samples(database: psycopg.Connection) -> list[float]:
    values: list[float] = []
    for _ in range(5):
        started = time.perf_counter()
        with database.cursor() as cursor:
            cursor.execute(SIGNAL_READ_SQL)
            cursor.fetchall()
        values.append(round((time.perf_counter() - started) * 1000, 3))
    return values


def median(values: list[float]) -> float:
    return sorted(values)[len(values) // 2]


def main() -> None:
    with psycopg.connect(
        host=os.getenv("PGHOST", "postgres"), port=os.getenv("PGPORT", "5432"),
        dbname=os.getenv("PGDATABASE", "n8n"), user=os.getenv("PGUSER", "n8n"),
        password=os.getenv("PGPASSWORD", ""),
    ) as database:
        baseline = samples(database)
        with database.cursor() as cursor:
            cursor.execute(TEMP_INDEX_SQL)
        database.commit()
        try:
            indexed = samples(database)
        finally:
            with database.cursor() as cursor:
                cursor.execute("DROP INDEX IF EXISTS quant.tmp_scan_signal_read_idx")
            database.commit()
    print(json.dumps({
        "statement_count": 1,
        "baseline_elapsed_ms": baseline,
        "indexed_elapsed_ms": indexed,
        "baseline_median_ms": median(baseline),
        "indexed_median_ms": median(indexed),
        "scope": "local compose PostgreSQL; temporary local index is removed; no owner tunnel/provider access",
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
