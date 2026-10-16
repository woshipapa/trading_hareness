"""Bounded persistence adapter for derived intraday signal attribution."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Callable, Iterable

from psycopg.types.json import Json


_DEFAULT_BATCH_SIZE = 1_000
_SIGNAL_ROWS_SQL = """SELECT signal_event_id,signal_key,signal_type,conditions,evidence
             FROM quant.intraday_signal_events
            WHERE state IN ('confirmed','alerted')
              AND signal_type IN ('entry','watch','reduce','exit')
              AND observed_at<=%s"""
_UPDATE_ATTRIBUTION_SQL = (
    "UPDATE quant.intraday_signal_events SET evidence=%s WHERE signal_event_id=%s"
)


def _row_batches(result: Any, batch_size: int) -> Iterable[list[Any]]:
    """Stream psycopg rows when possible, retaining a compatibility fallback."""
    fetchmany = getattr(result, "fetchmany", None)
    if callable(fetchmany):
        first = fetchmany(batch_size)
        if isinstance(first, (list, tuple)):
            batch = list(first)
            while batch:
                yield batch
                batch = list(fetchmany(batch_size))
            return
    fetchall = getattr(result, "fetchall", None)
    rows = fetchall() if callable(fetchall) else []
    rows = list(rows or [])
    for start in range(0, len(rows), batch_size):
        yield rows[start:start + batch_size]


def _write_batch(connection: Any, rows: list[tuple[Any, Any]]) -> None:
    if not rows:
        return
    cursor_factory = getattr(connection, "cursor", None)
    if callable(cursor_factory):
        with cursor_factory() as cursor:
            cursor.executemany(_UPDATE_ATTRIBUTION_SQL, rows)
        return
    for params in rows:
        connection.execute(_UPDATE_ATTRIBUTION_SQL, params)


def refresh_signal_attributions(
    connection: Any,
    *,
    cutoff: datetime,
    attribution_fn: Callable[..., dict[str, Any]],
    json_safe: Callable[[Any], Any],
    batch_size: int = _DEFAULT_BATCH_SIZE,
) -> int:
    """Refresh changed derived labels with bounded reads and writes.

    Signal evidence remains immutable except for its derived ``attribution``
    projection.  The point-in-time filter and settleable signal states are
    unchanged; only the transport boundary is batched so a large recompute
    does not spend one tunnel round trip per row.
    """
    size = max(1, int(batch_size))
    result = connection.execute(_SIGNAL_ROWS_SQL, (cutoff,))
    changed = 0
    for rows in _row_batches(result, size):
        updates: list[tuple[Any, Any]] = []
        for row in rows:
            evidence = dict(row["evidence"] or {})
            attribution = attribution_fn(
                str(row["signal_key"]), str(row["signal_type"]),
                dict(row["conditions"] or {}), evidence,
            )
            if evidence.get("attribution") == attribution:
                continue
            evidence["attribution"] = attribution
            updates.append((Json(json_safe(evidence)), row["signal_event_id"]))
        _write_batch(connection, updates)
        changed += len(updates)
    return changed


__all__ = ["refresh_signal_attributions"]
