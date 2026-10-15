"""Read-only persistence boundary for the intraday runtime status panel.

This module deliberately contains no provider client and no scheduler logic.
The dashboard can inspect a degraded decision path without causing market-data
traffic or mutating alert delivery state.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any


LATEST_SCAN_ROWS_SQL = """
    (SELECT 'latest' AS kind,status,observed_at,source_status,summary
       FROM quant.intraday_scan_runs
      ORDER BY observed_at DESC LIMIT 1)
    UNION ALL
    (SELECT 'latest_completed' AS kind,status,observed_at,source_status,summary
       FROM quant.intraday_scan_runs
      WHERE status='completed'
      ORDER BY observed_at DESC LIMIT 1)
"""

LATEST_BOARD_ROWS_SQL = """
    (SELECT 'board' AS kind,status,observed_at,source_status,summary,NULL::jsonb AS coverage
       FROM quant.intraday_board_reports
      ORDER BY observed_at DESC LIMIT 1)
    UNION ALL
    (SELECT 'curve' AS kind,status,observed_at,source_status,NULL::jsonb AS summary,coverage
       FROM quant.intraday_board_flow_snapshots
      ORDER BY observed_at DESC LIMIT 1)
"""

# The status board is a read-only control-plane projection.  Keep all of its
# bounded reads in one database statement so the remote owner tunnel pays one
# round trip while PostgreSQL still evaluates every subquery in one snapshot.
# JSONB is used only as the row envelope; `_runtime_evidence_from_snapshot`
# restores the timestamp fields that downstream status projections compare as
# datetimes.  No old result is cached across requests.
RUNTIME_STATUS_SNAPSHOT_SQL = """
WITH health_rows AS (
    SELECT provider_key,capability,consecutive_failures,circuit_open_until,last_success_at,
           last_failure_at,last_error,last_latency_ms,last_row_count,updated_at
      FROM quant.provider_health
     WHERE provider_key IN ('longhuvip','tencent_free','tushare_super_sdk','tushare_super_get','tushare_super')
       AND capability IN ('realtime_quote','order_book_quote','rt_k','rt_min','rt_min_daily')
), quote_rows AS (
    SELECT source_name,max(observed_at) AS last_observed_at,count(*)::int AS rows
      FROM quant.intraday_quote_observations
     WHERE source_name IN ('longhu_order_book','tencent_free','tencent_order_book','tushare_super_get_rt_k')
     GROUP BY source_name
), raw_rows AS (
    SELECT api_name,max(available_at) AS last_observed_at,count(*)::int AS rows
      FROM quant.tushare_raw_records
     WHERE api_name IN ('rt_k','rt_min','rt_min_daily')
     GROUP BY api_name
), minute_profile AS (
    SELECT max(available_at) AS last_observed_at,count(*)::int AS rows,
           max(trading_date) AS latest_trading_date
      FROM quant.intraday_minute_sessions
     WHERE source_name='tencent_intraday_minutes'
), scan_rows AS (
    (SELECT 'latest' AS kind,status,observed_at,source_status,summary
       FROM quant.intraday_scan_runs
      ORDER BY observed_at DESC LIMIT 1)
    UNION ALL
    (SELECT 'latest_completed' AS kind,status,observed_at,source_status,summary
       FROM quant.intraday_scan_runs
      WHERE status='completed'
      ORDER BY observed_at DESC LIMIT 1)
), rule_input_snapshots AS (
    SELECT
        (SELECT count(*)::int FROM quant.intraday_rule_input_snapshots) AS total,
        (SELECT count(*)::int FROM quant.intraday_rule_input_snapshots
          WHERE inputs->>'schema_version'='intraday-rule-input-v2') AS v2,
        (SELECT count(*)::int FROM quant.intraday_rule_input_snapshots
          WHERE inputs->>'schema_version'='intraday-rule-input-v1') AS v1,
        (SELECT max(observed_at) FROM quant.intraday_rule_input_snapshots) AS latest_observed_at
), board_rows AS (
    (SELECT 'board' AS kind,status,observed_at,source_status,summary,NULL::jsonb AS coverage
       FROM quant.intraday_board_reports
      ORDER BY observed_at DESC LIMIT 1)
    UNION ALL
    (SELECT 'curve' AS kind,status,observed_at,source_status,NULL::jsonb AS summary,coverage
       FROM quant.intraday_board_flow_snapshots
      ORDER BY observed_at DESC LIMIT 1)
), delivery_rows AS (
    SELECT 'signal' AS kind,status,sent_at,created_at,error_message
      FROM quant.intraday_alert_deliveries
     ORDER BY created_at DESC LIMIT 20
), pending_delivery_counts AS (
    SELECT
        (SELECT count(*)::int FROM quant.intraday_alert_deliveries
          WHERE channel='feishu_adapter' AND status IN ('pending','failed')
            AND message_text IS NOT NULL AND message_text<>''
            AND attempt_count<%s) AS signal_count,
        (SELECT count(*)::int FROM quant.intraday_board_rotation_deliveries
          WHERE channel='feishu_adapter' AND status IN ('pending','failed')
            AND attempt_count<%s) AS rotation_count
), latest_daily_summary AS (
    SELECT exchange_date,delivery_status,attempt_count,next_attempt_at,sent_at,error_message,updated_at
      FROM quant.strategy_day_summaries
     ORDER BY exchange_date DESC LIMIT 1
), latest_health_event AS (
    SELECT event_type,streak_count,delivery_status,error_message,created_at,updated_at
      FROM quant.alert_delivery_health_events
     WHERE channel='feishu_adapter'
     ORDER BY created_at DESC LIMIT 1
), watch_row AS (
    SELECT count(*)::int AS enabled
      FROM quant.intraday_watchlists
     WHERE enabled
)
SELECT 'health_rows' AS kind,
       COALESCE((SELECT jsonb_agg(to_jsonb(row_data)) FROM (SELECT * FROM health_rows) row_data), '[]'::jsonb) AS value
UNION ALL
SELECT 'quote_rows',
       COALESCE((SELECT jsonb_agg(to_jsonb(row_data)) FROM (SELECT * FROM quote_rows) row_data), '[]'::jsonb)
UNION ALL
SELECT 'raw_rows',
       COALESCE((SELECT jsonb_agg(to_jsonb(row_data)) FROM (SELECT * FROM raw_rows) row_data), '[]'::jsonb)
UNION ALL
SELECT 'minute_profile',
       COALESCE((SELECT to_jsonb(row_data) FROM (SELECT * FROM minute_profile) row_data), '{}'::jsonb)
UNION ALL
SELECT 'scan_rows',
       COALESCE((SELECT jsonb_agg(to_jsonb(row_data)) FROM (SELECT * FROM scan_rows) row_data), '[]'::jsonb)
UNION ALL
SELECT 'rule_input_snapshots',
       COALESCE((SELECT to_jsonb(row_data) FROM (SELECT * FROM rule_input_snapshots) row_data), '{}'::jsonb)
UNION ALL
SELECT 'board_rows',
       COALESCE((SELECT jsonb_agg(to_jsonb(row_data)) FROM (SELECT * FROM board_rows) row_data), '[]'::jsonb)
UNION ALL
SELECT 'delivery_rows',
       COALESCE((SELECT jsonb_agg(to_jsonb(row_data) ORDER BY row_data.created_at DESC)
                   FROM (SELECT * FROM delivery_rows) row_data), '[]'::jsonb)
UNION ALL
SELECT 'pending_delivery_counts',
       COALESCE((SELECT to_jsonb(row_data) FROM (SELECT * FROM pending_delivery_counts) row_data), '{}'::jsonb)
UNION ALL
SELECT 'latest_daily_summary',
       COALESCE((SELECT to_jsonb(row_data) FROM (SELECT * FROM latest_daily_summary) row_data), 'null'::jsonb)
UNION ALL
SELECT 'latest_health_event',
       COALESCE((SELECT to_jsonb(row_data) FROM (SELECT * FROM latest_health_event) row_data), 'null'::jsonb)
UNION ALL
SELECT 'watch_row',
       COALESCE((SELECT to_jsonb(row_data) FROM (SELECT * FROM watch_row) row_data), '{"enabled":0}'::jsonb)
"""


_STATUS_DATETIME_FIELDS: dict[str, frozenset[str]] = {
    "health_rows": frozenset({"circuit_open_until", "last_success_at", "last_failure_at", "updated_at"}),
    "quote_rows": frozenset({"last_observed_at"}),
    "raw_rows": frozenset({"last_observed_at"}),
    "minute_profile": frozenset({"last_observed_at"}),
    "scan_rows": frozenset({"observed_at"}),
    "board_rows": frozenset({"observed_at"}),
    "delivery_rows": frozenset({"sent_at", "created_at"}),
    "latest_daily_summary": frozenset({"next_attempt_at", "sent_at", "updated_at"}),
    "latest_health_event": frozenset({"created_at", "updated_at"}),
}


def _json_snapshot_value(value: Any, default: Any) -> Any:
    """Accept psycopg JSONB values and lightweight string-based test doubles."""
    if value is None:
        return default
    if isinstance(value, (str, bytes, bytearray)):
        try:
            return json.loads(value)
        except (TypeError, ValueError):
            return default
    return value


def _status_datetime(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return value
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _status_rows(value: Any, fields: frozenset[str]) -> list[dict[str, Any]]:
    rows = _json_snapshot_value(value, [])
    if not isinstance(rows, list):
        return []
    normalized: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        item = dict(row)
        for key in fields:
            if key in item:
                item[key] = _status_datetime(item[key])
        normalized.append(item)
    return normalized


def _status_row(value: Any, fields: frozenset[str], default: Any = None) -> Any:
    item = _json_snapshot_value(value, default)
    if not isinstance(item, dict):
        return default
    normalized = dict(item)
    for key in fields:
        if key in normalized:
            normalized[key] = _status_datetime(normalized[key])
    return normalized


def _runtime_evidence_from_snapshot(rows: list[Any]) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for raw in rows:
        if not raw:
            continue
        row = dict(raw)
        kind = row.get("kind")
        if kind:
            values[str(kind)] = row.get("value")

    health_rows = _status_rows(values.get("health_rows"), _STATUS_DATETIME_FIELDS["health_rows"])
    quote_rows = _status_rows(values.get("quote_rows"), _STATUS_DATETIME_FIELDS["quote_rows"])
    raw_rows = _status_rows(values.get("raw_rows"), _STATUS_DATETIME_FIELDS["raw_rows"])
    minute_profile = _status_row(values.get("minute_profile"), _STATUS_DATETIME_FIELDS["minute_profile"], {}) or {}
    scan_rows = _status_rows(values.get("scan_rows"), _STATUS_DATETIME_FIELDS["scan_rows"])
    scan_partition = _partition_status_rows(scan_rows)
    board_rows = _status_rows(values.get("board_rows"), _STATUS_DATETIME_FIELDS["board_rows"])
    board_partition = _partition_status_rows(board_rows)
    delivery_rows = _status_rows(values.get("delivery_rows"), _STATUS_DATETIME_FIELDS["delivery_rows"])
    latest_delivery = delivery_rows[0] if delivery_rows else None
    pending_counts = _status_row(values.get("pending_delivery_counts"), frozenset(), {}) or {}
    latest_daily_summary = _status_row(values.get("latest_daily_summary"), _STATUS_DATETIME_FIELDS["latest_daily_summary"])
    latest_health_event = _status_row(values.get("latest_health_event"), _STATUS_DATETIME_FIELDS["latest_health_event"])
    watch_row = _status_row(values.get("watch_row"), frozenset(), {"enabled": 0}) or {"enabled": 0}
    return {
        "health_rows": health_rows,
        "quote_rows": quote_rows,
        "raw_rows": raw_rows,
        "minute_profile": minute_profile,
        "latest_scan": scan_partition.get("latest"),
        "latest_completed_scan": scan_partition.get("latest_completed"),
        "rule_input_snapshots": _status_row(values.get("rule_input_snapshots"), frozenset(), {}) or {},
        "latest_board": board_partition.get("board"),
        "latest_board_curve": board_partition.get("curve"),
        "latest_delivery": latest_delivery,
        "delivery_history": [{"status": row.get("status")} for row in delivery_rows],
        "pending_delivery_count": int(pending_counts.get("signal_count") or 0),
        "pending_rotation_delivery_count": int(pending_counts.get("rotation_count") or 0),
        "latest_daily_summary": latest_daily_summary,
        "latest_health_event": latest_health_event,
        "watch_row": watch_row,
    }


def _partition_status_rows(rows: list[Any]) -> dict[str, dict[str, Any]]:
    """Split a bounded UNION result while preserving database-native values."""
    partitioned: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not row:
            continue
        item = dict(row)
        kind = item.pop("kind", None)
        if kind:
            if kind == "board":
                item.pop("coverage", None)
            elif kind == "curve":
                item.pop("summary", None)
            partitioned[str(kind)] = item
    return partitioned


def load_intraday_runtime_evidence(database: Any, max_alert_attempts: int) -> dict[str, Any]:
    """Load the bounded evidence set used by the runtime health endpoint."""
    with database.transaction() as connection:
        rows = connection.execute(
            RUNTIME_STATUS_SNAPSHOT_SQL,
            (max_alert_attempts, max_alert_attempts),
        ).fetchall()
    return _runtime_evidence_from_snapshot(rows)


async def load_intraday_runtime_evidence_async(async_database: Any, max_alert_attempts: int) -> dict[str, Any]:
    """Native async equivalent used by the frontend status route.

    This is deliberately a read-only projection.  It does not call a market
    provider, mutate delivery state, or acquire a scheduler lease.
    """
    async with async_database.transaction() as connection:
        result = await connection.execute(
            RUNTIME_STATUS_SNAPSHOT_SQL,
            (max_alert_attempts, max_alert_attempts),
        )
        rows = await result.fetchall()
    return _runtime_evidence_from_snapshot(rows)


__all__ = ["load_intraday_runtime_evidence", "load_intraday_runtime_evidence_async"]
