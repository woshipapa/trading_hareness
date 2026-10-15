"""Verify the one-statement latest-scan JSONB snapshot on local PostgreSQL."""

from __future__ import annotations

import json
import os
import time

import psycopg
from psycopg.rows import dict_row

from app.intraday_evidence_read_model import LATEST_SCAN_SNAPSHOT_SQL


LEGACY_SCAN_SQL = "SELECT * FROM quant.intraday_scan_runs ORDER BY observed_at DESC LIMIT 1"
LEGACY_SIGNALS_SQL = "SELECT * FROM quant.intraday_signal_events WHERE scan_id=%s ORDER BY severity DESC,created_at LIMIT %s"
LEGACY_DELIVERIES_SQL = """SELECT d.* FROM quant.intraday_alert_deliveries d
    JOIN quant.intraday_signal_events s ON s.signal_event_id=d.signal_event_id
   WHERE s.scan_id=%s ORDER BY d.created_at LIMIT %s"""


def measure_snapshot(database: psycopg.Connection) -> tuple[float, dict[str, object] | None]:
    started = time.perf_counter()
    with database.cursor() as cursor:
        cursor.execute(LATEST_SCAN_SNAPSHOT_SQL, (200, 200))
        row = cursor.fetchone()
    return round((time.perf_counter() - started) * 1000, 3), row


def measure_legacy(database: psycopg.Connection) -> float:
    started = time.perf_counter()
    with database.cursor() as cursor:
        cursor.execute(LEGACY_SCAN_SQL)
        scan = cursor.fetchone()
        if scan:
            cursor.execute(LEGACY_SIGNALS_SQL, (scan["scan_id"], 200))
            cursor.fetchall()
            cursor.execute(LEGACY_DELIVERIES_SQL, (scan["scan_id"], 200))
            cursor.fetchall()
    return round((time.perf_counter() - started) * 1000, 3)


def main() -> None:
    with psycopg.connect(
        host=os.getenv("PGHOST", "postgres"), port=os.getenv("PGPORT", "5432"),
        dbname=os.getenv("PGDATABASE", "n8n"), user=os.getenv("PGUSER", "n8n"),
        password=os.getenv("PGPASSWORD", ""), row_factory=dict_row,
    ) as database:
        snapshot_samples: list[float] = []
        legacy_samples: list[float] = []
        row = None
        for _ in range(5):
            elapsed_ms, row = measure_snapshot(database)
            snapshot_samples.append(elapsed_ms)
            legacy_samples.append(measure_legacy(database))
    print(json.dumps({
        "statement_count": 1,
        "legacy_statement_count": 3,
        "snapshot_elapsed_ms": snapshot_samples,
        "legacy_elapsed_ms": legacy_samples,
        "snapshot_median_ms": sorted(snapshot_samples)[len(snapshot_samples) // 2],
        "legacy_median_ms": sorted(legacy_samples)[len(legacy_samples) // 2],
        "has_scan": bool(row and row.get("scan")),
        "signal_rows": len((row or {}).get("signals") or []),
        "delivery_rows": len((row or {}).get("deliveries") or []),
        "scope": "local compose PostgreSQL; no owner tunnel/provider access",
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
