"""Persistence for derived multiscale market-flow research evidence."""

from __future__ import annotations

import bisect
import time as time_module
from collections.abc import Callable
from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from psycopg.types.json import Json

from .market_flow_features import board_flow_breadth, intraday_flow_state, volume_flow_regime
from .sector_flow_repository import rebuild_sector_flow_daily_features


CHINA = ZoneInfo("Asia/Shanghai")
REBUILD_BATCH = 60
#: The post-close stage gives market_flow_features 90 s, and a timed-out caller cannot cancel the
#: executor thread running the rebuild. So the rebuild ends itself first: it stops between batches
#: once this budget is spent, and a batch is one transaction, so nothing is left half-written.
REBUILD_BUDGET_SECONDS = 75.0


def _items(row: Any) -> list[dict[str, Any]]:
    return list((dict(row.get("payload") or {}) if row else {}).get("items") or [])


def _feature_status(features: dict[str, Any]) -> str:
    if not features.get("board_count"):
        return "insufficient"
    return "partial" if features.get("quality_flags") else "ready"


_INSERT_FEATURE_SQL = """INSERT INTO quant.market_flow_feature_snapshots(
               feature_key,exchange_date,cadence,observed_at,source_snapshot_minute,status,market_state,
               concept_count,concept_positive_ratio,concept_median_flow,concept_mean_change_pct,
               five_minute_positive_ratio_delta,session_positive_ratio_delta,afternoon_repair_strength,
               market_amount,market_volume,amount_change_pct,volume_change_pct,advancer_ratio,features,quality_flags)
           VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
           ON CONFLICT(feature_key) DO UPDATE SET
             observed_at=EXCLUDED.observed_at,source_snapshot_minute=EXCLUDED.source_snapshot_minute,
             status=EXCLUDED.status,market_state=EXCLUDED.market_state,concept_count=EXCLUDED.concept_count,
             concept_positive_ratio=EXCLUDED.concept_positive_ratio,concept_median_flow=EXCLUDED.concept_median_flow,
             concept_mean_change_pct=EXCLUDED.concept_mean_change_pct,
             five_minute_positive_ratio_delta=EXCLUDED.five_minute_positive_ratio_delta,
             session_positive_ratio_delta=EXCLUDED.session_positive_ratio_delta,
             afternoon_repair_strength=EXCLUDED.afternoon_repair_strength,
             market_amount=EXCLUDED.market_amount,market_volume=EXCLUDED.market_volume,
             amount_change_pct=EXCLUDED.amount_change_pct,volume_change_pct=EXCLUDED.volume_change_pct,
             advancer_ratio=EXCLUDED.advancer_ratio,features=EXCLUDED.features,
             quality_flags=EXCLUDED.quality_flags,updated_at=now()"""


def _feature_params(
    *,
    feature_key: str,
    exchange_date: Any,
    cadence: str,
    observed_at: datetime,
    source_snapshot_minute: datetime | None,
    status: str,
    market_state: str,
    features: dict[str, Any],
) -> tuple[Any, ...]:
    return (
        feature_key, exchange_date, cadence, observed_at, source_snapshot_minute, status, market_state,
        int(features.get("board_count") or features.get("concept_board_count") or 0),
        features.get("positive_ratio", features.get("concept_positive_ratio")),
        features.get("median_flow"), features.get("mean_change_pct"),
        features.get("five_minute_positive_ratio_delta"), features.get("session_positive_ratio_delta"),
        features.get("afternoon_repair_strength"), features.get("market_amount"), features.get("market_volume"),
        features.get("amount_change_pct"), features.get("volume_change_pct"), features.get("advancer_ratio"),
        Json(features), Json(sorted(set(features.get("quality_flags") or []))),
    )


def _insert_feature(connection: Any, **values: Any) -> None:
    connection.execute(_INSERT_FEATURE_SQL, _feature_params(**values))


def _session_bounds(local: datetime) -> tuple[datetime, datetime]:
    """UTC session start (09:30, or 09:20 before it) and afternoon start of the minute's exchange date."""
    exchange_date = local.date()
    session_start_time = time(9, 30) if local.time() >= time(9, 30) else time(9, 20)
    session_start = datetime.combine(exchange_date, session_start_time, tzinfo=CHINA).astimezone(timezone.utc)
    afternoon_start = datetime.combine(exchange_date, time(13, 0), tzinfo=CHINA).astimezone(timezone.utc)
    return session_start, afternoon_start


def _minute_state(
    current: Any,
    five_minute: Any,
    session_reference: Any,
    afternoon_min: float | None,
) -> tuple[str, dict[str, Any]]:
    """Status and features of one minute from its stored board snapshot payloads."""
    features = intraday_flow_state(
        board_flow_breadth(_items(current)),
        five_minute_reference=board_flow_breadth(_items(five_minute)) if five_minute else None,
        session_reference=board_flow_breadth(_items(session_reference)) if session_reference else None,
        afternoon_min_positive_ratio=afternoon_min,
    )
    return _feature_status(features), features


def persist_intraday_market_flow_feature(
    database: Any,
    snapshot_minute: datetime,
    observed_at: datetime,
) -> dict[str, Any]:
    """Derive one coverage-gated minute state from already stored raw snapshots (the live path)."""
    local = snapshot_minute.astimezone(CHINA)
    exchange_date = local.date()
    session_start, afternoon_start = _session_bounds(local)
    with database.transaction() as connection:
        current = connection.execute(
            """SELECT payload FROM quant.intraday_board_flow_snapshots
                 WHERE snapshot_minute=%s AND status IN ('completed','partial')""",
            (snapshot_minute,),
        ).fetchone()
        if current is None:
            return {"status": "insufficient", "state": "insufficient", "quality_flags": ["source_snapshot_missing"]}
        five_minute = connection.execute(
            """SELECT payload FROM quant.intraday_board_flow_snapshots
                 WHERE snapshot_minute<=%s AND snapshot_minute>=%s
                   AND status IN ('completed','partial')
                 ORDER BY snapshot_minute DESC LIMIT 1""",
            (snapshot_minute - timedelta(minutes=5), session_start),
        ).fetchone()
        session_reference = connection.execute(
            """SELECT payload FROM quant.intraday_board_flow_snapshots
                 WHERE snapshot_minute>=%s AND snapshot_minute<=%s
                   AND status IN ('completed','partial')
                 ORDER BY snapshot_minute LIMIT 1""",
            (session_start, snapshot_minute),
        ).fetchone()
        afternoon_min = None
        if local.time() >= time(13, 0):
            row = connection.execute(
                """SELECT min(concept_positive_ratio) AS minimum
                     FROM quant.market_flow_feature_snapshots
                    WHERE exchange_date=%s AND cadence='minute'
                      AND observed_at>=%s AND observed_at<%s
                      AND concept_positive_ratio IS NOT NULL""",
                (exchange_date, afternoon_start, observed_at),
            ).fetchone()
            afternoon_min = float(row["minimum"]) if row and row["minimum"] is not None else None
        status, features = _minute_state(current, five_minute, session_reference, afternoon_min)
        _insert_feature(
            connection,
            feature_key=f"minute:{snapshot_minute.isoformat()}", exchange_date=exchange_date,
            cadence="minute", observed_at=observed_at, source_snapshot_minute=snapshot_minute,
            status=status, market_state=str(features["state"]), features=features,
        )
    return {"status": status, **features}


def persist_market_snapshot_flow_feature(
    connection: Any,
    *,
    session: str,
    exchange_date: Any,
    observed_at: datetime,
    summary: dict[str, Any],
) -> dict[str, Any]:
    """Attach prior-close volume and latest intraday breadth to a market snapshot."""
    previous = connection.execute(
        """SELECT summary FROM quant.market_snapshot_runs
             WHERE session='close' AND exchange_date<%s
             ORDER BY exchange_date DESC,observed_at DESC LIMIT 1""",
        (exchange_date,),
    ).fetchone()
    latest_flow = connection.execute(
        """SELECT observed_at,concept_count,concept_positive_ratio,concept_median_flow,
                  concept_mean_change_pct,market_state,features,quality_flags
             FROM quant.market_flow_feature_snapshots
            WHERE exchange_date=%s AND cadence='minute' AND observed_at<=%s
            ORDER BY observed_at DESC LIMIT 1""",
        (exchange_date, observed_at),
    ).fetchone()
    concept_flow = {
        "board_count": int(latest_flow["concept_count"]),
        "positive_ratio": float(latest_flow["concept_positive_ratio"]),
        "median_flow": float(latest_flow["concept_median_flow"]) if latest_flow["concept_median_flow"] is not None else None,
        "mean_change_pct": float(latest_flow["concept_mean_change_pct"]) if latest_flow["concept_mean_change_pct"] is not None else None,
    } if latest_flow and latest_flow["concept_positive_ratio"] is not None else None
    features = volume_flow_regime(
        summary,
        previous_close_summary=dict(previous["summary"] or {}) if previous else None,
        concept_flow=concept_flow,
    )
    features["source_minute_market_state"] = latest_flow["market_state"] if latest_flow else None
    status = "partial" if features["quality_flags"] else "ready"
    if features["market_amount"] is None and features["market_volume"] is None:
        status = "insufficient"
    _insert_feature(
        connection,
        feature_key=f"snapshot:{exchange_date}:{session}", exchange_date=exchange_date,
        cadence=session, observed_at=observed_at,
        source_snapshot_minute=latest_flow["observed_at"] if latest_flow else None,
        status=status, market_state=str(features["state"]), features=features,
    )
    return {"status": status, **features}


def _minute_features_in_memory(minute_rows: list[Any], stored: list[Any]) -> Any:
    """Yield (row, status, features) chronologically, reproducing the live path's three lookups.

    The live path asks the database for the snapshot five minutes back, the session's first snapshot
    and the afternoon's lowest stored positive ratio. Here all of a range's snapshots are already in
    memory. The afternoon minimum is kept over every stored minute feature of the day, with this
    run's recomputed ones replacing theirs as the rebuild advances, as the live path's upserts do.
    """
    by_day: dict[Any, list[Any]] = {}
    for row in minute_rows:
        by_day.setdefault(row["snapshot_minute"].astimezone(CHINA).date(), []).append(row)
    minutes_by_day = {day: [row["snapshot_minute"] for row in rows] for day, rows in by_day.items()}
    ratios: dict[str, tuple[Any, datetime, float | None]] = {
        str(row["feature_key"]): (row["exchange_date"], row["observed_at"],
                                  float(row["concept_positive_ratio"]) if row["concept_positive_ratio"] is not None else None)
        for row in stored
    }
    for row in minute_rows:
        snapshot_minute, observed_at = row["snapshot_minute"], row["observed_at"]
        local = snapshot_minute.astimezone(CHINA)
        exchange_date = local.date()
        session_start, afternoon_start = _session_bounds(local)
        day_rows, day_minutes = by_day[exchange_date], minutes_by_day[exchange_date]
        five_index = bisect.bisect_right(day_minutes, snapshot_minute - timedelta(minutes=5)) - 1
        five_minute = day_rows[five_index] if five_index >= 0 and day_minutes[five_index] >= session_start else None
        session_index = bisect.bisect_left(day_minutes, session_start)
        session_reference = (day_rows[session_index]
                             if session_index < len(day_minutes) and day_minutes[session_index] <= snapshot_minute else None)
        afternoon_min = None
        if local.time() >= time(13, 0):
            values = [ratio for day, at, ratio in ratios.values()
                      if day == exchange_date and ratio is not None and afternoon_start <= at < observed_at]
            afternoon_min = min(values) if values else None
        status, features = _minute_state(row, five_minute, session_reference, afternoon_min)
        positive = features.get("positive_ratio", features.get("concept_positive_ratio"))
        ratios[f"minute:{snapshot_minute.isoformat()}"] = (exchange_date, observed_at,
                                                           float(positive) if positive is not None else None)
        yield row, status, features


def rebuild_stored_market_flow_features(
    database: Any,
    start_date: date,
    end_date: date,
    *,
    budget_seconds: float | None = None,
    clock: Callable[[], float] = time_module.monotonic,
) -> dict[str, Any]:
    """Rebuild derived features from local evidence without provider calls.

    Minute rows are processed chronologically so afternoon-repair features can only see earlier
    same-day observations. The range's snapshots are read in one query and the features written in
    batches of ``REBUILD_BATCH``, one transaction each: one round trip per batch, not five per
    minute, which over the owner's database tunnel overran the stage's 90 s (2026-10-10). Market
    snapshots are rebuilt afterwards, so their close and midday rows reference the same day's
    derived minute state.

    With ``budget_seconds`` the rebuild stops between batches once the budget is spent and reports
    ``partial`` with the last minute written; a rerun continues idempotently (upserts by key).
    """
    if end_date < start_date:
        raise ValueError("end_date must not be before start_date")
    if (end_date - start_date).days > 45:
        raise ValueError("stored feature rebuild is capped at 45 calendar days")
    started = clock()
    over_budget = (lambda: clock() - started >= budget_seconds) if budget_seconds else (lambda: False)
    start_utc = datetime.combine(start_date, time.min, tzinfo=CHINA).astimezone(timezone.utc)
    end_utc = datetime.combine(end_date + timedelta(days=1), time.min, tzinfo=CHINA).astimezone(timezone.utc)
    with database.transaction() as connection:
        minute_rows = connection.execute(
            """SELECT snapshot_minute,observed_at,payload
                 FROM quant.intraday_board_flow_snapshots
                WHERE snapshot_minute>=%s AND snapshot_minute<%s
                  AND status IN ('completed','partial')
                ORDER BY snapshot_minute""",
            (start_utc, end_utc),
        ).fetchall()
        stored = connection.execute(
            """SELECT feature_key,exchange_date,observed_at,concept_positive_ratio
                 FROM quant.market_flow_feature_snapshots
                WHERE cadence='minute' AND exchange_date BETWEEN %s AND %s""",
            (start_date, end_date),
        ).fetchall()
        snapshot_rows = connection.execute(
            """SELECT DISTINCT ON(exchange_date,session)
                      exchange_date,session,observed_at,summary
                 FROM quant.market_snapshot_runs
                WHERE exchange_date BETWEEN %s AND %s
                  AND session IN ('midday','close')
                  AND status IN ('ready','degraded')
                ORDER BY exchange_date,session,observed_at DESC""",
            (start_date, end_date),
        ).fetchall()

    minute_counts = {"ready": 0, "partial": 0, "insufficient": 0}
    batch: list[tuple[Any, ...]] = []
    written = 0
    last_written: datetime | None = None
    stopped = False

    def flush() -> None:
        nonlocal written, last_written, batch
        if not batch:
            return
        with database.transaction() as connection:
            with connection.cursor() as cursor:
                cursor.executemany(_INSERT_FEATURE_SQL, batch)
        written += len(batch)
        last_written = batch[-1][3]
        batch = []

    for row, status, features in _minute_features_in_memory(list(minute_rows), list(stored)):
        minute_counts[status] = minute_counts.get(status, 0) + 1
        batch.append(_feature_params(
            feature_key=f"minute:{row['snapshot_minute'].isoformat()}",
            exchange_date=row["snapshot_minute"].astimezone(CHINA).date(), cadence="minute",
            observed_at=row["observed_at"], source_snapshot_minute=row["snapshot_minute"],
            status=status, market_state=str(features["state"]), features=features,
        ))
        if len(batch) >= REBUILD_BATCH:
            flush()
            if over_budget():
                stopped = True
                break
    if not stopped:
        flush()

    snapshot_counts = {"ready": 0, "partial": 0, "insufficient": 0}
    sector_daily: dict[str, Any] | None = None
    if not stopped and not over_budget():
        for row in snapshot_rows:
            with database.transaction() as connection:
                result = persist_market_snapshot_flow_feature(
                    connection,
                    session=str(row["session"]),
                    exchange_date=row["exchange_date"],
                    observed_at=row["observed_at"],
                    summary=dict(row["summary"] or {}),
                )
            result_status = str(result.get("status") or "insufficient")
            snapshot_counts[result_status] = snapshot_counts.get(result_status, 0) + 1
        sector_daily = rebuild_sector_flow_daily_features(database, start_date, end_date)
    else:
        stopped = True
    return {
        "status": "partial" if stopped else "completed",
        "reason": (f"the {budget_seconds:.0f}s budget ran out after {written} of {len(minute_rows)} minutes; "
                   "a rerun continues from the stored rows") if stopped else None,
        "start_date": str(start_date),
        "end_date": str(end_date),
        "source": "stored_evidence_only",
        "provider_calls": 0,
        "minute_rows": len(minute_rows),
        "minutes_written": written,
        "last_minute_written_at": last_written.isoformat() if last_written else None,
        "minute_status_counts": minute_counts,
        "snapshot_rows": len(snapshot_rows),
        "snapshot_status_counts": snapshot_counts,
        "sector_daily": sector_daily,
        "elapsed_seconds": round(clock() - started, 3),
        "decision_eligible": False,
    }


__all__ = [
    "REBUILD_BATCH",
    "REBUILD_BUDGET_SECONDS",
    "persist_intraday_market_flow_feature",
    "persist_market_snapshot_flow_feature",
    "rebuild_stored_market_flow_features",
]
