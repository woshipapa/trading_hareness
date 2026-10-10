"""Compute, store and read the daily sentiment temperature (decision 0013).

The post-close stage ``market_temperature`` calls ``refresh``. It recomputes the
series over the last ``LOOKBACK_DAYS`` calendar days in one PostgreSQL pass
(about 13 s on the owner database) and stores the recent readings.

Each reading is a ``market_temperature_daily`` observation (provider
``local_derived``, no symbol), one per session, with ``effective_at`` set to
that session's close. A changed reading, after the bars were repaired, is
stored beside the old one, and the read takes the newest.

A reading identical to the newest one already stored is skipped here. The
table's unique key cannot do it: it includes the symbol, and NULLs never
conflict, so re-storing 30 readings a night would have added 30 duplicates.

The request path only reads stored rows; it never rescans the bars.

    python -m app.market_temperature_runtime --end 2026-10-09 [--keep 400] [--apply]

Without ``--apply`` it only prints the latest readings. ``--keep 400`` stores the
whole history once (a backfill).
"""

from __future__ import annotations

import argparse
import json
from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from .market_temperature import BOILING, COMPONENTS, FREEZING, VERSION, temperature_series
from .market_temperature_repository import daily_rows
from .public_market_repository import persist_timed_observations

PROVIDER_KEY = "local_derived"
CAPABILITY = "market_temperature_daily"
LOOKBACK_DAYS = 420          # about 290 sessions: the 250-session window plus warm-up
KEEP_SESSIONS = 30           # readings re-stored each evening; identical ones are skipped
CN_TZ = ZoneInfo("Asia/Shanghai")

READ_SQL = """
SELECT DISTINCT ON (effective_at) effective_at, normalized
  FROM quant.raw_market_observations
 WHERE provider_key=%s AND capability=%s AND symbol IS NULL AND effective_at >= %s AND effective_at <= %s
 ORDER BY effective_at, available_at DESC
"""


def session_close(day: date) -> datetime:
    return datetime.combine(day, time(15, 0), CN_TZ)


def compute(connection: Any, end: date, *, lookback_days: int = LOOKBACK_DAYS) -> list[dict[str, Any]]:
    """Every reading from ``end - lookback_days`` to ``end``; early ones lack a temperature (warm-up)."""
    return temperature_series(daily_rows(connection, end - timedelta(days=lookback_days), end))


VOLATILE = frozenset({"effective_at", "available_at", "provider_key", "capability"})


def _content(reading: dict[str, Any]) -> str:
    """A reading as comparable text: what was computed, not when it was stored."""
    return json.dumps({key: value for key, value in reading.items() if key not in VOLATILE},
                      sort_keys=True, ensure_ascii=False, default=str)


def _stored_newest(connection: Any, first: date, last: date) -> dict[str, str]:
    rows = connection.execute(READ_SQL, (PROVIDER_KEY, CAPABILITY, session_close(first), session_close(last))).fetchall()
    newest = {}
    for row in rows:
        payload = dict(row)["normalized"]
        payload = json.loads(payload) if isinstance(payload, str) else dict(payload)
        newest[str(payload.get("trade_date"))] = _content(payload)
    return newest


def refresh(database: Any, end: date, *, keep: int = KEEP_SESSIONS, apply: bool = True) -> dict[str, Any]:
    """Recompute and store the last ``keep`` readings that changed; ``completed`` only when ``end`` itself has one."""
    with database.transaction() as connection:
        readings = compute(connection, end)
        scored = [reading for reading in readings if reading["temperature"] is not None][-keep:]
        stored = _stored_newest(connection, date.fromisoformat(scored[0]["trade_date"]), end) if scored else {}
    now = datetime.now(timezone.utc).isoformat()
    rows = []
    for reading in scored:
        row = {**reading, "version": VERSION, "research_only": True, "live_effect": "none"}
        if stored.get(reading["trade_date"]) == _content(row):
            continue
        rows.append({**row, "effective_at": session_close(date.fromisoformat(reading["trade_date"])).isoformat(),
                     "available_at": now})
    written = persist_timed_observations(database, PROVIDER_KEY, CAPABILITY, rows) if apply and rows else 0
    latest = scored[-1] if scored else None
    status = "completed" if latest and latest["trade_date"] == end.isoformat() else "blocked"
    return {
        "status": status, "trade_date": end.isoformat(), "stored": written, "unchanged": len(scored) - len(rows),
        "readings": len(scored),
        "latest": {key: latest[key] for key in ("trade_date", "temperature", "band")} if latest else None,
        "reason": None if status == "completed" else "no temperature for the session yet: its bars or limits are missing",
        "research_only": True, "live_effect": "none",
    }


def daily_series(connection: Any, *, days: int = 120, end: date | None = None) -> dict[str, Any]:
    """The newest stored reading of each session, oldest first, the last ``days`` of them."""
    last = end or datetime.now(CN_TZ).date()
    since = session_close(last - timedelta(days=days * 7 // 5 + 14))
    rows = connection.execute(READ_SQL, (PROVIDER_KEY, CAPABILITY, since, session_close(last))).fetchall()
    readings = []
    for row in rows:
        payload = dict(row)["normalized"]
        payload = json.loads(payload) if isinstance(payload, str) else dict(payload)
        readings.append({key: value for key, value in payload.items() if key not in {"provider_key", "capability"}})
    return {
        "version": VERSION, "readings": readings[-days:],
        "thresholds": {"freezing": FREEZING, "boiling": BOILING},
        "components": [{"key": item.key, "label": item.label, "direction": item.direction} for item in COMPONENTS],
        "research_only": True, "live_effect": "none",
    }


def main(argv: list[str] | None = None, *, database: Any = None) -> int:
    parser = argparse.ArgumentParser(description="daily sentiment temperature (decision 0013)")
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    parser.add_argument("--keep", type=int, default=KEEP_SESSIONS)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    if database is None:
        from .database import Database
        database = Database()
        database.open()
    result = refresh(database, args.end, keep=args.keep, apply=args.apply)
    print(json.dumps(result, ensure_ascii=False, default=str))
    return 0 if result["status"] == "completed" else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())


__all__ = ["CAPABILITY", "KEEP_SESSIONS", "LOOKBACK_DAYS", "PROVIDER_KEY", "compute", "daily_series", "main", "refresh",
           "session_close"]
