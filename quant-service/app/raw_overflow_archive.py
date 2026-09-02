"""Bounded raw-evidence overflow coordination for the edge profile.

The edge collector owns PostgreSQL and the Feishu adapter owns Baidu OAuth.
This module is the small, authenticated hand-off between them: it exposes a
keyset-paginated batch, records a verified remote object, and advances the
cursor only after the adapter reports an immutable hash/size/row-count ACK.

It intentionally does not read from Baidu and never participates in a live
strategy decision.  A failed or interrupted upload leaves the cursor in place;
the next request therefore replays the same bounded batch safely.
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from .raw_overflow_policy import RawOverflowLimits, classify


DEFAULT_CAPABILITIES = (
    "a_share_prices_snapshot",
    "realtime_quote",
    "order_book_quote",
    "rt_k",
    "rt_min",
    "rt_min_daily",
)
DEFAULT_STREAM_PREFIX = "raw_market_observations:"


def _truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _bounded_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        return min(maximum, max(minimum, int(value)))
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class RawOverflowConfig:
    enabled: bool
    capabilities: tuple[str, ...]
    hot_window_hours: int = 24
    batch_rows: int = 500
    limits: RawOverflowLimits = RawOverflowLimits()

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "RawOverflowConfig":
        env = os.environ if environ is None else environ
        configured = tuple(
            item.strip() for item in str(env.get("QUANT_RAW_OVERFLOW_CAPABILITIES", "") or "").split(",")
            if item.strip()
        )
        capabilities = configured or DEFAULT_CAPABILITIES
        warning = _bounded_float(env.get("QUANT_RAW_OVERFLOW_WARNING_RATIO"), 0.80, 0.50, 0.98)
        stop = _bounded_float(env.get("QUANT_RAW_OVERFLOW_STOP_RATIO"), 0.90, warning, 0.99)
        return cls(
            enabled=_truthy(env.get("QUANT_RAW_OVERFLOW_ARCHIVE_ENABLED")),
            capabilities=tuple(dict.fromkeys(capabilities)),
            hot_window_hours=_bounded_int(env.get("QUANT_RAW_OVERFLOW_HOT_WINDOW_HOURS"), 24, 1, 24 * 30),
            batch_rows=_bounded_int(env.get("QUANT_RAW_OVERFLOW_BATCH_ROWS"), 500, 1, 2_000),
            limits=RawOverflowLimits(
                warning_ratio=warning,
                stop_ratio=stop,
                max_batch_bytes=_bounded_int(env.get("QUANT_RAW_OVERFLOW_MAX_BATCH_BYTES"), 256 * 1024 * 1024, 1 * 1024 * 1024, 256 * 1024 * 1024),
                max_spool_bytes=_bounded_int(env.get("QUANT_RAW_OVERFLOW_MAX_SPOOL_BYTES"), 256 * 1024 * 1024, 1 * 1024 * 1024, 256 * 1024 * 1024),
                max_queue_batches=_bounded_int(env.get("QUANT_RAW_OVERFLOW_MAX_QUEUE_BATCHES"), 1_000, 1, 1_000),
            ),
        )


def _bounded_float(value: Any, default: float, minimum: float, maximum: float) -> float:
    try:
        return min(maximum, max(minimum, float(value)))
    except (TypeError, ValueError):
        return default


def stream_key(capability: str) -> str:
    value = str(capability or "").strip()
    if not value or ":" in value or len(value) > 120:
        raise ValueError("invalid raw overflow capability")
    return f"{DEFAULT_STREAM_PREFIX}{value}"


def capability_from_stream(stream: str, config: RawOverflowConfig) -> str:
    value = str(stream or "").strip()
    prefix = DEFAULT_STREAM_PREFIX
    if not value.startswith(prefix):
        raise ValueError("invalid raw overflow stream")
    capability = value[len(prefix):]
    if capability not in config.capabilities:
        raise ValueError("raw overflow stream is not allowlisted")
    return capability


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return (value if value.tzinfo else value.replace(tzinfo=timezone.utc)).astimezone(timezone.utc).isoformat()
    return str(value)


def _offset(row: Mapping[str, Any] | None) -> dict[str, str] | None:
    if not row or not row.get("effective_at") or not row.get("observation_id"):
        return None
    return {"effective_at": _iso(row["effective_at"]) or "", "observation_id": str(row["observation_id"])}


def _storage_state(connection: Any, config: RawOverflowConfig) -> tuple[str, tuple[str, ...], dict[str, Any]]:
    row = connection.execute(
        """SELECT coalesce(sum(pg_total_relation_size(c.oid)),0)::bigint AS bytes
             FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname='quant' AND c.relkind IN ('r','m','p')""",
    ).fetchone() or {}
    hot_used = int(row.get("bytes") or 0)
    hot_budget = _bounded_int(os.getenv("QUANT_HOT_DATABASE_SOFT_BYTES"), 36 * 1024**3, 1 * 1024**3, 36 * 1024**3)
    queue = connection.execute(
        """SELECT count(*)::int AS count FROM quant.raw_archive_batches
            WHERE status IN ('queued','uploading','retryable_failed')""",
    ).fetchone() or {}
    spool = 0
    state, reasons = classify(
        hot_used_bytes=hot_used, hot_budget_bytes=hot_budget,
        queue_batches=int(queue.get("count") or 0), spool_bytes=spool,
        cloud_enabled=config.enabled, limits=config.limits,
    )
    return state, reasons, {
        "hot_database_bytes": hot_used,
        "hot_database_budget_bytes": hot_budget,
        "queue_batches": int(queue.get("count") or 0),
        "spool_bytes": spool,
    }


def status(database: Any, *, config: RawOverflowConfig | None = None) -> dict[str, Any]:
    """Return token-free archive state for health/UI consumers."""
    config = config or RawOverflowConfig.from_env()
    with database.transaction() as connection:
        state, reasons, storage = _storage_state(connection, config)
        offsets = connection.execute(
            """SELECT stream_key,capability,effective_at,observation_id,updated_at,state,last_error
                 FROM quant.raw_archive_offsets ORDER BY stream_key""",
        ).fetchall()
        batches = connection.execute(
            """SELECT count(*) FILTER (WHERE status IN ('queued','uploading','retryable_failed'))::int AS queue_depth,
                       count(*) FILTER (WHERE status='verified')::int AS verified,
                       count(*) FILTER (WHERE status='failed')::int AS failed,
                       max(updated_at) FILTER (WHERE status='verified') AS last_verified_at
                  FROM quant.raw_archive_batches""",
        ).fetchone() or {}
    return {
        "enabled": config.enabled,
        "state": state,
        "reasons": list(reasons),
        "capabilities": list(config.capabilities),
        "hot_window_hours": config.hot_window_hours,
        "batch_rows": config.batch_rows,
        "storage": storage,
        "offsets": [
            {"stream_key": row["stream_key"], "capability": row["capability"], "offset": _offset(row),
             "updated_at": _iso(row.get("updated_at")), "state": row.get("state"), "last_error": row.get("last_error")}
            for row in offsets
        ],
        "queue": {
            "queue_depth": int(batches.get("queue_depth") or 0),
            "verified": int(batches.get("verified") or 0),
            "failed": int(batches.get("failed") or 0),
            "last_verified_at": _iso(batches.get("last_verified_at")),
        },
        "live_effect": "none",
        "cold_store_is_not_a_decision_input": True,
    }


def next_batch(database: Any, *, stream: str, limit: int | None = None,
               config: RawOverflowConfig | None = None) -> dict[str, Any]:
    """Read one bounded, replay-safe keyset batch.

    No claim is made here: only ``ack_batch`` can move the cursor.  This is
    deliberate because an HTTP disconnect must not make rows disappear.
    """
    config = config or RawOverflowConfig.from_env()
    capability = capability_from_stream(stream, config)
    requested = _bounded_int(limit, config.batch_rows, 1, config.batch_rows)
    with database.transaction() as connection:
        state, reasons, storage = _storage_state(connection, config)
        if not config.enabled:
            return {"status": "disabled", "stream_key": stream, "state": state, "reasons": list(reasons), "rows": []}
        if state == "normal":
            return {"status": "not_needed", "stream_key": stream, "state": state, "reasons": list(reasons), "rows": []}
        connection.execute(
            """INSERT INTO quant.raw_archive_offsets(stream_key,capability,state)
                 VALUES(%s,%s,%s) ON CONFLICT(stream_key) DO NOTHING""",
            (stream, capability, state),
        )
        cursor = connection.execute(
            """SELECT effective_at,observation_id FROM quant.raw_archive_offsets
                WHERE stream_key=%s FOR UPDATE""", (stream,),
        ).fetchone() or {}
        cutoff = datetime.now(timezone.utc) - timedelta(hours=config.hot_window_hours)
        params: list[Any] = [capability, cutoff]
        cursor_clause = ""
        if cursor.get("effective_at") and cursor.get("observation_id"):
            cursor_clause = " AND (effective_at, observation_id) > (%s,%s)"
            params.extend([cursor["effective_at"], cursor["observation_id"]])
        params.append(requested)
        rows = connection.execute(
            f"""SELECT observation_id,provider_key,capability,market,symbol,effective_at,available_at,
                              ingested_at,availability_basis,payload_sha256,normalized,payload,fetch_run_id,created_at
                           FROM quant.raw_market_observations
                          WHERE capability=%s AND effective_at<%s {cursor_clause}
                       ORDER BY effective_at,observation_id LIMIT %s""", params,
        ).fetchall()
        encoded = []
        for row in rows:
            item = {key: value for key, value in dict(row).items()}
            for key in ("observation_id", "fetch_run_id"):
                if item.get(key) is not None:
                    item[key] = str(item[key])
            for key in ("effective_at", "available_at", "ingested_at", "created_at"):
                item[key] = _iso(item.get(key))
            encoded.append(item)
        before = _offset(cursor)
    if not encoded:
        return {"status": "empty", "stream_key": stream, "state": state, "reasons": list(reasons), "storage": storage, "rows": [], "before_offset": before}
    first = {"effective_at": encoded[0]["effective_at"], "observation_id": encoded[0]["observation_id"]}
    last = {"effective_at": encoded[-1]["effective_at"], "observation_id": encoded[-1]["observation_id"]}
    body = "".join(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n" for row in encoded).encode()
    return {
        "status": "ready", "stream_key": stream, "capability": capability, "state": state,
        "reasons": list(reasons), "storage": storage, "before_offset": before,
        "first_offset": first, "last_offset": last, "row_count": len(encoded),
        "estimated_jsonl_bytes": len(body), "rows": encoded,
    }


def acknowledge(database: Any, payload: Mapping[str, Any], *, config: RawOverflowConfig | None = None) -> dict[str, Any]:
    """Record remote verification and advance the cursor atomically."""
    config = config or RawOverflowConfig.from_env()
    stream = str(payload.get("stream_key") or "").strip()
    capability = capability_from_stream(stream, config)
    before = payload.get("before_offset") or None
    first = payload.get("first_offset") or {}
    last = payload.get("last_offset") or {}
    row_count = _bounded_int(payload.get("row_count"), 0, 1, 2_000)
    compressed_bytes = _bounded_int(payload.get("compressed_bytes"), 0, 1, config.limits.max_batch_bytes)
    sha256 = str(payload.get("sha256") or "").strip().lower()
    if not row_count or not compressed_bytes or len(sha256) != 64 or any(c not in "0123456789abcdef" for c in sha256):
        raise ValueError("raw overflow ACK metadata is invalid")
    try:
        batch_id = uuid.UUID(str(payload.get("batch_id")))
    except (ValueError, TypeError, AttributeError) as error:
        raise ValueError("raw overflow ACK batch_id is invalid") from error
    if not first.get("effective_at") or not first.get("observation_id") or not last.get("effective_at") or not last.get("observation_id"):
        raise ValueError("raw overflow ACK offset is incomplete")
    remote_path = str(payload.get("remote_path") or "").strip()[:500] or None
    remote_fs_id = str(payload.get("remote_fs_id") or "").strip()[:120] or None
    with database.transaction() as connection:
        connection.execute(
            """INSERT INTO quant.raw_archive_offsets(stream_key,capability,state)
                 VALUES(%s,%s,'cloud_overflow') ON CONFLICT(stream_key) DO NOTHING""", (stream, capability),
        )
        cursor = connection.execute(
            """SELECT effective_at,observation_id FROM quant.raw_archive_offsets
                WHERE stream_key=%s FOR UPDATE""", (stream,),
        ).fetchone() or {}
        current = _offset(cursor)
        if current != before:
            existing = connection.execute(
                "SELECT batch_id::text,remote_path,remote_fs_id FROM quant.raw_archive_batches WHERE batch_id=%s AND status='verified'",
                (batch_id,),
            ).fetchone()
            if existing:
                return {"status": "already_verified", "batch_id": str(existing["batch_id"]), "offset": current}
            raise ValueError("raw overflow ACK cursor does not match current offset")
        cutoff = datetime.now(timezone.utc) - timedelta(hours=config.hot_window_hours)
        next_clause = ""
        next_params: list[Any] = [capability, cutoff]
        if current:
            next_clause = " AND (effective_at, observation_id) > (%s,%s)"
            next_params.extend([current["effective_at"], current["observation_id"]])
        first_row = connection.execute(
            f"""SELECT effective_at,observation_id FROM quant.raw_market_observations
                  WHERE capability=%s AND effective_at<%s {next_clause}
               ORDER BY effective_at,observation_id LIMIT 1""", next_params,
        ).fetchone()
        if not first_row or _offset(first_row) != first:
            raise ValueError("raw overflow ACK first offset is not the next durable row")
        range_count = connection.execute(
            """SELECT count(*)::int AS count FROM quant.raw_market_observations
                WHERE capability=%s AND effective_at<%s
                  AND (effective_at,observation_id)>=(%s::timestamptz,%s::uuid)
                  AND (effective_at,observation_id)<=(%s::timestamptz,%s::uuid)""",
            (capability, cutoff, first["effective_at"], first["observation_id"], last["effective_at"], last["observation_id"]),
        ).fetchone() or {}
        if int(range_count.get("count") or 0) != row_count:
            raise ValueError("raw overflow ACK row count does not match the durable source range")
        connection.execute(
            """INSERT INTO quant.raw_archive_batches(
                    batch_id,stream_key,capability,first_effective_at,first_observation_id,
                    last_effective_at,last_observation_id,row_count,compressed_bytes,sha256,status,
                    remote_path,remote_fs_id)
                VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'verified',%s,%s)
                ON CONFLICT(stream_key,first_effective_at,first_observation_id,last_effective_at,last_observation_id,sha256)
                DO UPDATE SET status='verified',remote_path=EXCLUDED.remote_path,remote_fs_id=EXCLUDED.remote_fs_id,updated_at=now()""",
            (batch_id, stream, capability, first["effective_at"], first["observation_id"], last["effective_at"], last["observation_id"], row_count, compressed_bytes, sha256, remote_path, remote_fs_id),
        )
        connection.execute(
            """UPDATE quant.raw_archive_offsets
                   SET effective_at=%s,observation_id=%s,updated_at=now(),state='cloud_overflow',last_error=NULL
                 WHERE stream_key=%s""", (last["effective_at"], last["observation_id"], stream),
        )
        # Delete only the exact prefix just verified and only outside the hot
        # window. This bounds the edge table while preserving decision data.
        deleted = connection.execute(
            """DELETE FROM quant.raw_market_observations
                WHERE capability=%s AND effective_at<%s
                  AND (effective_at,observation_id)<=((%s)::timestamptz,%s::uuid)""",
            (capability, cutoff, last["effective_at"], last["observation_id"]),
        ).rowcount
    return {"status": "verified", "batch_id": str(batch_id), "offset": last, "deleted_rows": int(deleted or 0)}


def failure(database: Any, payload: Mapping[str, Any], *, config: RawOverflowConfig | None = None) -> dict[str, Any]:
    config = config or RawOverflowConfig.from_env()
    stream = str(payload.get("stream_key") or "").strip()
    capability = capability_from_stream(stream, config)
    error = str(payload.get("error") or "archive upload failed").strip()[:500]
    with database.transaction() as connection:
        connection.execute(
            """INSERT INTO quant.raw_archive_offsets(stream_key,capability,state,last_error)
                 VALUES(%s,%s,'cloud_overflow',%s)
                 ON CONFLICT(stream_key) DO UPDATE SET state='cloud_overflow',last_error=%s,updated_at=now()""",
            (stream, capability, error, error),
        )
    return {"status": "recorded", "stream_key": stream, "error": error}


__all__ = [
    "DEFAULT_CAPABILITIES", "RawOverflowConfig", "acknowledge", "capability_from_stream",
    "failure", "next_batch", "status", "stream_key",
]
