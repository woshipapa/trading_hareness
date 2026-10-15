"""Bounded read-only views of stored intraday watch, scan, and decision evidence."""

from __future__ import annotations

import json
from typing import Any, Callable


LATEST_SCAN_SNAPSHOT_SQL = """
WITH latest AS (
    SELECT to_jsonb(scan) AS scan
      FROM (
        SELECT * FROM quant.intraday_scan_runs
         ORDER BY observed_at DESC
         LIMIT 1
      ) scan
), signals AS (
    SELECT COALESCE(
        jsonb_agg(rows.row_json ORDER BY rows.severity DESC, rows.created_at),
        '[]'::jsonb
    ) AS value
      FROM (
        SELECT to_jsonb(e) AS row_json, e.severity, e.created_at
          FROM quant.intraday_signal_events e
         WHERE e.scan_id=(SELECT (scan->>'scan_id')::uuid FROM latest)
         ORDER BY e.severity DESC,e.created_at
         LIMIT %s
      ) rows
), deliveries AS (
    SELECT COALESCE(
        jsonb_agg(rows.row_json ORDER BY rows.created_at),
        '[]'::jsonb
    ) AS value
      FROM (
        SELECT to_jsonb(d) AS row_json, d.created_at
          FROM quant.intraday_alert_deliveries d
          JOIN quant.intraday_signal_events e ON e.signal_event_id=d.signal_event_id
         WHERE e.scan_id=(SELECT (scan->>'scan_id')::uuid FROM latest)
         ORDER BY d.created_at
         LIMIT %s
      ) rows
)
SELECT (SELECT scan FROM latest) AS scan,
       signals.value AS signals,
       deliveries.value AS deliveries
  FROM signals CROSS JOIN deliveries
"""


def _json_value(value: Any, default: Any) -> Any:
    """Return a JSONB value from psycopg while tolerating contract fakes."""
    if value is None:
        return default
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (TypeError, ValueError):
            return default
    return value


def watchlists(database: Any) -> dict[str, Any]:
    with database.transaction() as connection:
        rows = connection.execute(
            "SELECT * FROM quant.intraday_watchlists ORDER BY enabled DESC,available_quantity DESC,updated_at DESC,symbol"
        ).fetchall()
    return {"items": rows, "notice": "观察/持仓池用于提醒范围，不会创建或提交任何订单。"}


def latest_scan(database: Any, *, limit: int = 100) -> dict[str, Any]:
    """Return a bounded snapshot of one already-persisted scan and its outbox evidence."""
    bounded_limit = max(1, min(limit, 200))
    with database.transaction() as connection:
        row = connection.execute(LATEST_SCAN_SNAPSHOT_SQL, (bounded_limit, bounded_limit)).fetchone()
    if not row:
        return {"scan": None, "signals": [], "deliveries": []}
    return {
        "scan": _json_value(row.get("scan") if hasattr(row, "get") else row["scan"], None),
        "signals": _json_value(row.get("signals") if hasattr(row, "get") else row["signals"], []),
        "deliveries": _json_value(row.get("deliveries") if hasattr(row, "get") else row["deliveries"], []),
    }


def decision_card(database: Any, symbol: str, decision_card_fn: Callable[[Any, str], dict[str, Any]]) -> dict[str, Any]:
    with database.transaction() as connection:
        return decision_card_fn(connection, symbol)


__all__ = ["decision_card", "latest_scan", "watchlists"]
