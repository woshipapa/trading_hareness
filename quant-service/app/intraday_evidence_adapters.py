"""Fixed SSD-replay adapters for the owner research tables.

Adapters accept only validated observation payloads and contain all SQL in one
module.  The spool decides *when* a stream is enabled; this module decides
*where* an enabled stream may be written.  No adapter promotes evidence into a
strategy or order path.
"""

from __future__ import annotations

from typing import Any, Mapping

from psycopg.types.json import Json


def _executemany(connection: Any, sql: str, rows: list[tuple[Any, ...]]) -> int:
    if not rows:
        return 0
    cursor_factory = getattr(connection, "cursor", None)
    if callable(cursor_factory):
        with cursor_factory() as cursor:
            cursor.executemany(sql, rows)
            return len(rows)
    for row in rows:
        connection.execute(sql, row)
    return len(rows)


def insert_raw_market_observations(connection: Any, observations: list[Mapping[str, Any]]) -> int:
    sql = """INSERT INTO quant.raw_market_observations(
                 provider_key,capability,market,symbol,effective_at,available_at,
                 payload_sha256,normalized,payload)
               VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT(provider_key,capability,market,symbol,effective_at,payload_sha256) DO NOTHING"""
    rows = [(
        item["provider_key"], item["capability"], item["market"], item["symbol"],
        item["effective_at"], item["available_at"], item["payload_sha256"],
        Json(item["normalized"]), Json(item["payload"]),
    ) for item in observations]
    return _executemany(connection, sql, rows)


def insert_quote_observations(connection: Any, observations: list[Mapping[str, Any]]) -> int:
    sql = """INSERT INTO quant.intraday_quote_observations(
                 scan_id,symbol,observed_at,source_name,price,pct_change,
                 volume_ratio,turnover_rate,main_net_inflow,raw)
               VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT(symbol,source_name,observed_at) DO NOTHING"""
    rows = []
    for item in observations:
        payload = item["payload"] if isinstance(item["payload"], Mapping) else {}
        rows.append((
            payload.get("scan_id"), payload.get("symbol") or item["symbol"],
            payload.get("observed_at") or item["effective_at"], payload.get("source_name") or item["capability"],
            payload.get("price"), payload.get("pct_change"), payload.get("volume_ratio"),
            payload.get("turnover_rate"), payload.get("main_net_inflow"), Json(payload.get("raw") or {}),
        ))
    return _executemany(connection, sql, rows)


def insert_board_flow_snapshots(connection: Any, observations: list[Mapping[str, Any]]) -> int:
    sql = """INSERT INTO quant.intraday_board_flow_snapshots(
                 snapshot_minute,observed_at,status,coverage,source_status,payload)
               VALUES(%s,%s,%s,%s,%s,%s)
               ON CONFLICT(snapshot_minute) DO UPDATE SET
                 observed_at=EXCLUDED.observed_at,status=EXCLUDED.status,
                 coverage=EXCLUDED.coverage,source_status=EXCLUDED.source_status,
                 payload=EXCLUDED.payload"""
    rows = []
    for item in observations:
        payload = item["payload"] if isinstance(item["payload"], Mapping) else {}
        rows.append((
            payload.get("snapshot_minute") or item["effective_at"],
            payload.get("observed_at") or item["captured_at"], payload.get("status") or "completed",
            Json(payload.get("coverage") or {}), Json(payload.get("source_status") or {}),
            Json(payload.get("payload") or payload),
        ))
    return _executemany(connection, sql, rows)


def insert_minute_sessions(connection: Any, observations: list[Mapping[str, Any]]) -> int:
    sql = """INSERT INTO quant.intraday_minute_sessions(
                 symbol,trading_date,minute_bucket,bar_time,open,high,low,close,
                 volume,amount,source_name,available_at,raw)
               VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT(symbol,trading_date,minute_bucket,source_name) DO UPDATE SET
                 bar_time=EXCLUDED.bar_time,open=EXCLUDED.open,high=EXCLUDED.high,
                 low=EXCLUDED.low,close=EXCLUDED.close,volume=EXCLUDED.volume,
                 amount=EXCLUDED.amount,available_at=EXCLUDED.available_at,raw=EXCLUDED.raw"""
    rows: list[tuple[Any, ...]] = []
    for item in observations:
        payload = item["payload"] if isinstance(item["payload"], Mapping) else {}
        source_rows = payload.get("rows") if isinstance(payload.get("rows"), list) else [payload]
        for row in source_rows:
            if not isinstance(row, Mapping):
                continue
            rows.append((
                row.get("symbol") or item["symbol"], row.get("trading_date"), row.get("minute_bucket"),
                row.get("bar_time") or item["effective_at"], row.get("open"), row.get("high"),
                row.get("low"), row.get("close"), row.get("volume"), row.get("amount"),
                row.get("source_name") or item["capability"], row.get("available_at") or item["available_at"],
                Json(row.get("raw") or row),
            ))
    return _executemany(connection, sql, rows)


REPLAY_ADAPTERS = {
    "raw_market_observations": insert_raw_market_observations,
    "intraday_quote_observations": insert_quote_observations,
    "intraday_board_flow_snapshots": insert_board_flow_snapshots,
    "intraday_minute_sessions": insert_minute_sessions,
}


__all__ = ["REPLAY_ADAPTERS", "insert_board_flow_snapshots", "insert_minute_sessions",
           "insert_quote_observations", "insert_raw_market_observations"]
