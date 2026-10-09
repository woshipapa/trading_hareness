"""Turn the stored per-symbol minute snapshots into minute documents, then retire them (decision 0009, step 2).

    python -m app.minute_cross_section_backfill convert --from 2026-09-07 --to 2026-10-09 [--apply]
    python -m app.minute_cross_section_backfill verify  --from ... --to ...
    python -m app.minute_cross_section_backfill delete  --from ... --to ... [--apply]
    python -m app.minute_cross_section_backfill radar   --from ... --to ... [--apply]

Every command is a dry run without ``--apply``. Each one runs only in the safe
window, weekdays 22:10-08:00 Beijing or any time at the weekend, because one
day is about 1.3 million rows read over the owner's database tunnel.

- ``convert`` writes a document for each minute that has none; a minute is
  one transaction, so a run can stop and resume anywhere.
- ``verify`` checks, minute by minute, the row count and a sample of rows
  field by field against the document.
- ``delete`` removes a day's per-symbol rows only after that day verifies, in
  batches with a pause between them. It frees space for reuse; the owner side
  then runs VACUUM (ANALYZE), never VACUUM FULL.
- ``radar`` replays the market radar over a day's documents. Without
  ``--apply`` it prints the closing point; with it, it stores the points, so a
  past day appears in the console.
"""

from __future__ import annotations

import argparse
import json
import time as clock
from collections.abc import Callable
from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from . import minute_cross_section as mcs

CN_TZ = ZoneInfo("Asia/Shanghai")
#: Keys the per-symbol writer added to each row; not part of the capture.
PERSIST_KEYS = ("provider_key", "capability", "record_index")
SAMPLE = 5
DELETE_BATCH = 20_000
DELETE_PAUSE_SECONDS = 0.5


def in_safe_window(now: datetime) -> bool:
    local = now.astimezone(CN_TZ)
    if local.weekday() >= 5:
        return True
    return local.time() >= time(22, 10) or local.time() < time(8, 0)


def _day_bounds(day: date) -> tuple[datetime, datetime]:
    start = datetime.combine(day, time(0, 0), CN_TZ)
    return start, start + timedelta(days=1)


def legacy_minutes(connection: Any, day: date) -> list[datetime]:
    start, end = _day_bounds(day)
    rows = connection.execute(
        """SELECT DISTINCT effective_at FROM quant.raw_market_observations
            WHERE capability=%s AND effective_at>=%s AND effective_at<%s ORDER BY 1""",
        (mcs.LEGACY_CAPABILITY, start, end)).fetchall()
    return [dict(row)["effective_at"] for row in rows]


def documented_minutes(connection: Any, day: date) -> set[datetime]:
    start, end = _day_bounds(day)
    rows = connection.execute(
        """SELECT effective_at FROM quant.raw_market_observations
            WHERE capability=%s AND symbol IS NULL AND effective_at>=%s AND effective_at<%s""",
        (mcs.CAPABILITY, start, end)).fetchall()
    return {dict(row)["effective_at"] for row in rows}


def legacy_rows(connection: Any, minute: datetime) -> list[dict[str, Any]]:
    """One stored minute in capture order, without the keys the per-symbol writer added."""
    rows = connection.execute(
        """SELECT normalized FROM quant.raw_market_observations WHERE capability=%s AND effective_at=%s""",
        (mcs.LEGACY_CAPABILITY, minute)).fetchall()
    documents = [dict(row)["normalized"] for row in rows]
    documents = [json.loads(item) if isinstance(item, str) else dict(item) for item in documents]
    documents.sort(key=lambda item: (item.get("record_index") is None, item.get("record_index") or 0,
                                     str(item.get("ts_code") or item.get("symbol") or "")))
    return [{key: value for key, value in item.items() if key not in PERSIST_KEYS} for item in documents]


def _document_at(connection: Any, minute: datetime) -> dict[str, Any] | None:
    found = mcs.first_between(connection, minute, minute + timedelta(microseconds=1))
    return found[1] if found else None


def convert_day(database: Any, day: date, *, apply: bool) -> dict[str, Any]:
    with database.transaction() as connection:
        minutes = legacy_minutes(connection, day)
        done = documented_minutes(connection, day)
    pending = [minute for minute in minutes if minute not in done]
    written = 0
    for minute in pending if apply else ():
        with database.transaction() as connection:
            rows = legacy_rows(connection, minute)
        if rows:
            mcs.persist_document(database, minute, rows, rows[0].get("snapshot_metadata") or {})
            written += 1
    return {"day": day.isoformat(), "minutes": len(minutes), "already_documented": len(minutes) - len(pending),
            "pending": len(pending), "written": written, "applied": apply}


def verify_day(database: Any, day: date) -> dict[str, Any]:
    problems: list[str] = []
    with database.transaction() as connection:
        minutes = legacy_minutes(connection, day)
        for minute in minutes:
            document = _document_at(connection, minute)
            if document is None:
                problems.append(f"{minute.isoformat()}: no document")
                continue
            rows = legacy_rows(connection, minute)
            if int(document.get("rows") or 0) != len(rows):
                problems.append(f"{minute.isoformat()}: {document.get('rows')} document rows vs {len(rows)} stored")
                continue
            step = max(1, len(rows) // SAMPLE)
            sample = list(range(0, len(rows), step))[:SAMPLE]
            if mcs.rows_of(document, sample) != [rows[index] for index in sample]:
                problems.append(f"{minute.isoformat()}: sampled rows differ")
    return {"day": day.isoformat(), "minutes": len(minutes), "ok": not problems and bool(minutes),
            "problems": problems[:20], "problem_count": len(problems)}


def delete_day(database: Any, day: date, *, apply: bool, pause: Callable[[float], None] = clock.sleep,
               batch: int = DELETE_BATCH) -> dict[str, Any]:
    verified = verify_day(database, day)
    if not verified["ok"]:
        return {"day": day.isoformat(), "deleted": 0, "refused": "the day does not verify", "verify": verified}
    start, end = _day_bounds(day)
    deleted = 0
    while apply:
        with database.transaction() as connection:
            result = connection.execute(
                """DELETE FROM quant.raw_market_observations WHERE ctid IN (
                       SELECT ctid FROM quant.raw_market_observations
                        WHERE capability=%s AND effective_at>=%s AND effective_at<%s LIMIT %s)""",
                (mcs.LEGACY_CAPABILITY, start, end, batch))
            removed = int(getattr(result, "rowcount", 0) or 0)
        deleted += removed
        if removed < batch:
            break
        pause(DELETE_PAUSE_SECONDS)
    return {"day": day.isoformat(), "deleted": deleted, "applied": apply, "minutes": verified["minutes"]}


def replay_radar(database: Any, day: date, *, apply: bool) -> dict[str, Any]:
    from .market_radar import RadarState, radar_point
    from .market_radar_runtime import CAPABILITY as RADAR_CAPABILITY, PROVIDER_KEY as RADAR_PROVIDER, session_limits
    from .public_market_repository import persist_timed_observations

    with database.transaction() as connection:
        documents = mcs.day_documents(connection, day)
        limits = session_limits(connection, day)
    state = RadarState(day)
    points = []
    for observed_at, document in documents:
        point = radar_point(mcs.rows_of(document), state, observed_at=observed_at.astimezone(CN_TZ), limits=limits or None)
        points.append({**point, "effective_at": observed_at.isoformat(),
                       "available_at": datetime.now(timezone.utc).isoformat(), "replayed": True})
    stored = persist_timed_observations(database, RADAR_PROVIDER, RADAR_CAPABILITY, points) if apply and points else 0
    closing = next((point for point in reversed(points) if "bands" in point), None)
    summary = None
    if closing:
        band = closing["bands"]["2"]
        summary = {"observed_at": closing["observed_at"], "pool_yi": round(closing["pool"]["turnover"] / 1e8, 1),
                   **{name: round(band[name]["turnover"] / 1e8, 1) for name in ("cum_down", "cum_up", "middle")},
                   "breadth": closing.get("breadth")}
    return {"day": day.isoformat(), "minutes": len(documents), "points": len(points), "stored": stored,
            "applied": apply, "closing": summary}


COMMANDS = {"convert": convert_day, "verify": verify_day, "delete": delete_day, "radar": replay_radar}


def _days(start: date, end: date) -> list[date]:
    return [start + timedelta(days=offset) for offset in range((end - start).days + 1)
            if (start + timedelta(days=offset)).weekday() < 5]


def main(argv: list[str] | None = None, *, database: Any = None, now: datetime | None = None) -> int:
    parser = argparse.ArgumentParser(description="minute cross-section backfill (decision 0009)")
    parser.add_argument("command", choices=sorted(COMMANDS))
    parser.add_argument("--from", dest="start", type=date.fromisoformat, required=True)
    parser.add_argument("--to", dest="end", type=date.fromisoformat, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    if not in_safe_window(now or datetime.now(timezone.utc)):
        print(json.dumps({"refused": "outside the safe window (weekdays 22:10-08:00 Beijing, or the weekend)"}))
        return 3
    if database is None:
        from .database import Database
        database = Database()
        database.open()
    for day in _days(args.start, args.end):
        command = COMMANDS[args.command]
        result = command(database, day) if args.command == "verify" else command(database, day, apply=args.apply)
        print(json.dumps(result, ensure_ascii=False, default=str), flush=True)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
