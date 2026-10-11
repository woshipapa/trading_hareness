"""One derived reading per session, kept as ``raw_market_observations`` rows.

These are the stored daily series of decision 0013: the sentiment temperature
and the broad-ETF flow. Each row has provider ``local_derived``, no symbol, and
``effective_at`` at its session's close.

``store`` skips a reading identical to the newest one already stored for its
session. The table's unique key cannot do this: it includes the symbol, and a
NULL symbol never conflicts. A changed reading, for example after the bars
were repaired, is stored beside the old one, and ``newest`` returns it.
"""

from __future__ import annotations

import json
from datetime import date, datetime, time, timezone
from typing import Any
from zoneinfo import ZoneInfo

from .public_market_repository import persist_timed_observations

PROVIDER_KEY = "local_derived"
CN_TZ = ZoneInfo("Asia/Shanghai")
STORAGE_KEYS = frozenset({"effective_at", "available_at", "provider_key", "capability"})

NEWEST_SQL = """
SELECT DISTINCT ON (effective_at) effective_at, normalized
  FROM quant.raw_market_observations
 WHERE provider_key=%s AND capability=%s AND symbol IS NULL AND effective_at >= %s AND effective_at <= %s
 ORDER BY effective_at, available_at DESC
"""


def session_close(day: date) -> datetime:
    return datetime.combine(day, time(15, 0), CN_TZ)


def _reading(row: Any) -> dict[str, Any]:
    payload = dict(row)["normalized"]
    payload = json.loads(payload) if isinstance(payload, str) else dict(payload)
    return {key: value for key, value in payload.items() if key not in STORAGE_KEYS}


def newest(connection: Any, capability: str, first: date, last: date) -> list[dict[str, Any]]:
    """The newest stored reading of each session from ``first`` to ``last``, oldest first."""
    rows = connection.execute(NEWEST_SQL, (PROVIDER_KEY, capability, session_close(first), session_close(last))).fetchall()
    return [_reading(row) for row in rows]


def _content(reading: dict[str, Any]) -> str:
    return json.dumps({key: value for key, value in reading.items() if key not in STORAGE_KEYS},
                      sort_keys=True, ensure_ascii=False, default=str)


def store(database: Any, capability: str, readings: list[dict[str, Any]]) -> dict[str, int]:
    """Store the readings (each with an ISO ``trade_date``) that differ from the newest stored ones."""
    if not readings:
        return {"stored": 0, "unchanged": 0}
    days = [date.fromisoformat(reading["trade_date"]) for reading in readings]
    with database.transaction() as connection:
        stored = {item.get("trade_date"): _content(item) for item in newest(connection, capability, min(days), max(days))}
    now = datetime.now(timezone.utc).isoformat()
    rows = [{**reading, "effective_at": session_close(day).isoformat(), "available_at": now}
            for reading, day in zip(readings, days) if stored.get(reading["trade_date"]) != _content(reading)]
    written = persist_timed_observations(database, PROVIDER_KEY, capability, rows) if rows else 0
    return {"stored": written, "unchanged": len(readings) - len(rows)}


__all__ = ["CN_TZ", "PROVIDER_KEY", "newest", "session_close", "store"]
