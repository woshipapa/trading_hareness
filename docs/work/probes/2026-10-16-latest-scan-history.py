"""Measure the bounded scan-performance history added to the latest-scan read."""

from __future__ import annotations

import json
import os
from statistics import median
import time

import psycopg
from psycopg.rows import dict_row

from app.intraday_evidence_read_model import LATEST_SCAN_SNAPSHOT_SQL


BASELINE_SQL = """
WITH latest AS (
    SELECT to_jsonb(scan) AS scan
      FROM (
        SELECT * FROM quant.intraday_scan_runs
         ORDER BY observed_at DESC
         LIMIT 1
      ) scan
), signals AS (
    SELECT COALESCE(jsonb_agg(rows.row_json ORDER BY rows.severity DESC, rows.created_at), '[]'::jsonb) AS value
      FROM (
        SELECT to_jsonb(e) AS row_json, e.severity, e.created_at
          FROM quant.intraday_signal_events e
         WHERE e.scan_id=(SELECT (scan->>'scan_id')::uuid FROM latest)
         ORDER BY e.severity DESC,e.created_at LIMIT %s
      ) rows
), deliveries AS (
    SELECT COALESCE(jsonb_agg(rows.row_json ORDER BY rows.created_at), '[]'::jsonb) AS value
      FROM (
        SELECT to_jsonb(d) AS row_json, d.created_at
          FROM quant.intraday_alert_deliveries d
          JOIN quant.intraday_signal_events e ON e.signal_event_id=d.signal_event_id
         WHERE e.scan_id=(SELECT (scan->>'scan_id')::uuid FROM latest)
         ORDER BY d.created_at LIMIT %s
      ) rows
)
SELECT (SELECT scan FROM latest) AS scan, signals.value AS signals, deliveries.value AS deliveries
  FROM signals CROSS JOIN deliveries
"""


def measure(database: psycopg.Connection, query: str) -> tuple[float, dict[str, object] | None]:
    started = time.perf_counter()
    with database.cursor() as cursor:
        cursor.execute(query, (200, 200))
        row = cursor.fetchone()
    return round((time.perf_counter() - started) * 1000, 3), row


def main() -> None:
    with psycopg.connect(
        host=os.getenv("PGHOST", "postgres"), port=os.getenv("PGPORT", "5432"),
        dbname=os.getenv("PGDATABASE", "n8n"), user=os.getenv("PGUSER", "n8n"),
        password=os.getenv("PGPASSWORD", ""), row_factory=dict_row,
    ) as database:
        baseline: list[float] = []
        history: list[float] = []
        row: dict[str, object] | None = None
        for _ in range(5):
            baseline_ms, _ = measure(database, BASELINE_SQL)
            history_ms, row = measure(database, LATEST_SCAN_SNAPSHOT_SQL)
            baseline.append(baseline_ms)
            history.append(history_ms)
    history_rows = (row or {}).get("history") or []
    print(json.dumps({
        "baseline_ms": baseline,
        "history_ms": history,
        "baseline_median_ms": median(baseline),
        "history_median_ms": median(history),
        "history_rows": len(history_rows) if isinstance(history_rows, list) else None,
        "has_scan": bool(row and row.get("scan")),
        "scope": "local compose PostgreSQL; no owner tunnel/provider access",
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
