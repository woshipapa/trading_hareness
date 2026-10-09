"""Point-in-time reads of the Fuyao limit events behind the limit-pool readers.

The licensed ``limit_list_ths`` and ``limit_step`` pools stopped on
2026-10-08 (decision 0005).  ``market_event_capture`` already stores the THS
limit-up pool every minute of the session and the ladder in
``quant.market_events``, so the pattern readers read those events.  The close
rule lives here once, for the synchronous and the async readers alike:

* a session's pool is its last pool snapshot, and only when that snapshot was
  taken at or after 14:57, the start of the closing call auction.  An earlier
  last snapshot means the capture stopped during the session: it is not the
  close pool, so it is reported unavailable rather than used;
* a reader for a date sees only events observed on that Shanghai date, and
  the previous session is the last close snapshot before that date.

Sources come from the data-source catalog, not from vendor names written
here.  Nothing in this module requests a provider.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from .datasources.catalog import primary_source
from .limit_event_fallback import close_pool_records, event_limit_record

CN_TZ = ZoneInfo("Asia/Shanghai")
#: The closing call auction starts at 14:57; a pool captured from then on is the close pool.
CLOSE_SNAPSHOT_FROM = time(14, 57)
#: How far back the previous session's close pool is looked for (covers the longest holiday).
PRIOR_SESSION_LOOKBACK = timedelta(days=14)

# Rows of one capture share ``occurred_at``; two captures inside the same
# minute upsert one minute identity, so the last minute is read whole.
_LAST_POOL_SNAPSHOT = """
    WITH last AS (
        SELECT max(occurred_at) AS at FROM quant.market_events
         WHERE event_type='limit_up_pool' AND source=%s AND occurred_at>=%s AND occurred_at<%s
    )
    SELECT DISTINCT ON(e.symbol) e.symbol,e.body,e.source,e.occurred_at,e.available_at
      FROM quant.market_events e
      JOIN last ON e.occurred_at>=date_trunc('minute',last.at)
               AND e.occurred_at<date_trunc('minute',last.at)+interval '1 minute'
     WHERE e.event_type='limit_up_pool' AND e.source=%s
     ORDER BY e.symbol,e.occurred_at DESC"""
# A rung is stored once per symbol, day and height; should a name show two
# heights in one day, the one first seen later stands.
_LADDER = """
    SELECT DISTINCT ON(symbol) symbol,body,source,occurred_at,available_at
      FROM quant.market_events
     WHERE event_type='limit_chain' AND source=%s AND occurred_at>=%s AND occurred_at<%s
     ORDER BY symbol,occurred_at DESC"""
_OTHER_POOLS = """
    SELECT DISTINCT ON(symbol) symbol,body,source,event_type,occurred_at,available_at
      FROM quant.market_events
     WHERE event_type='limit_up_pool' AND source<>%s AND occurred_at>=%s AND occurred_at<%s
     ORDER BY symbol,available_at DESC"""
_EVIDENCE_COUNTS = """
    SELECT event_type,count(*)::int AS rows,count(DISTINCT symbol)::int AS symbols,
           count(DISTINCT date_trunc('minute',occurred_at))::int AS snapshots,
           min(occurred_at) AS first_observed_at,max(occurred_at) AS last_observed_at
      FROM quant.market_events
     WHERE ((event_type='limit_up_pool' AND source=%s) OR (event_type='limit_chain' AND source=%s))
       AND occurred_at>=%s AND occurred_at<%s
     GROUP BY event_type"""


@dataclass(frozen=True)
class CloseLimitEvents:
    """One session's close pool, its ladder and the other limit-up pool sources."""

    pool: list[dict[str, Any]]
    ladder: list[dict[str, Any]]
    others: list[dict[str, Any]]
    snapshot: dict[str, Any]


def session_window(trade_date: date) -> tuple[datetime, datetime]:
    """The Shanghai calendar day of ``trade_date`` as an aware half-open range."""
    start = datetime.combine(trade_date, time(0), CN_TZ)
    return start, start + timedelta(days=1)


def _iso(value: Any) -> str | None:
    return value.isoformat() if isinstance(value, datetime) else None


def close_snapshot(rows: list[Any], source: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Keep a last pool snapshot only when the capture reached the closing auction."""
    values = [dict(row) for row in rows]
    observed = max((row["occurred_at"] for row in values if isinstance(row.get("occurred_at"), datetime)), default=None)
    local = observed.astimezone(CN_TZ) if observed else None
    closing = local is not None and local.time() >= CLOSE_SNAPSHOT_FROM
    snapshot: dict[str, Any] = {
        "status": "completed" if closing else "unavailable", "source": source,
        "trade_date": local.date().isoformat() if local else None, "observed_at": _iso(observed),
        "rows": len(values) if closing else 0,
    }
    if local is None:
        snapshot["reason"] = "no limit-up pool snapshot was captured for this date"
    elif not closing:
        snapshot["reason"] = f"the last limit-up pool snapshot ({local:%H:%M}) precedes the closing auction"
    return (values if closing else []), snapshot


def _queries(trade_date: date) -> tuple[str, dict[str, tuple[str, tuple[Any, ...]]]]:
    pool_source, ladder_source = primary_source("limits.limit_up_pool"), primary_source("limits.ladder")
    start, end = session_window(trade_date)
    return pool_source, {"pool": (_LAST_POOL_SNAPSHOT, (pool_source, start, end, pool_source)),
                         "ladder": (_LADDER, (ladder_source, start, end)),
                         "others": (_OTHER_POOLS, (pool_source, start, end))}


def _project(trade_date: date, pool_source: str, results: dict[str, list[Any]]) -> CloseLimitEvents:
    pool_rows, snapshot = close_snapshot(results["pool"], pool_source)
    pool, ladder = close_pool_records(pool_rows, [dict(row) for row in results["ladder"]], trade_date=trade_date)
    snapshot["ladder_rows"] = len(ladder)
    return CloseLimitEvents(pool=pool, ladder=ladder, others=[dict(row) for row in results["others"]],
                            snapshot=snapshot)


def load_close_limit_events(connection: Any, trade_date: date) -> CloseLimitEvents:
    """Read one session's close pool, ladder and other pool sources in the caller's transaction."""
    pool_source, queries = _queries(trade_date)
    return _project(trade_date, pool_source, {
        name: connection.execute(sql, params).fetchall() for name, (sql, params) in queries.items()
    })


async def load_close_limit_events_async(connection: Any, trade_date: date) -> CloseLimitEvents:
    """The same read on an async connection; the projection is shared."""
    pool_source, queries = _queries(trade_date)
    results: dict[str, list[Any]] = {}
    for name, (sql, params) in queries.items():
        result = await connection.execute(sql, params)
        results[name] = await result.fetchall()
    return _project(trade_date, pool_source, results)


def load_prior_close_pool(connection: Any, trade_date: date) -> list[dict[str, Any]]:
    """The previous session's close-pool rows, or none when it has no close snapshot."""
    source = primary_source("limits.limit_up_pool")
    start, _end = session_window(trade_date)
    rows, snapshot = close_snapshot(connection.execute(
        _LAST_POOL_SNAPSHOT, (source, start - PRIOR_SESSION_LOOKBACK, start, source),
    ).fetchall(), source)
    if not rows:
        return []
    prior_date = date.fromisoformat(snapshot["trade_date"])
    return [event_limit_record(row, trade_date=prior_date)["row_data"] for row in rows]


def limit_event_evidence(database: Any, trade_date: date) -> dict[str, Any]:
    """Report the captured limit evidence for one date; nothing is requested.

    This replaced the pre-mining refresh of the retired ``limit_list_ths``,
    ``limit_step`` and ``limit_cpt_list``: the pools are captured during the
    session, so before mining there is only something to check.  The report
    is ``blocked`` without a close snapshot, so a stage that depends on it
    stops instead of mining an intraday pool.  Concept limit strength has no
    reader in pattern mining and is reported unavailable, not derived.
    """
    pool_source, ladder_source = primary_source("limits.limit_up_pool"), primary_source("limits.ladder")
    start, end = session_window(trade_date)
    with database.transaction() as connection:
        counts = connection.execute(_EVIDENCE_COUNTS, (pool_source, ladder_source, start, end)).fetchall()
        pool_rows = connection.execute(_LAST_POOL_SNAPSHOT, (pool_source, start, end, pool_source)).fetchall()
    by_type = {str(row["event_type"]): dict(row) for row in counts}
    _rows, snapshot = close_snapshot(pool_rows, pool_source)
    pool, ladder = by_type.get("limit_up_pool", {}), by_type.get("limit_chain", {})
    report: dict[str, Any] = {
        "status": "completed" if snapshot["status"] == "completed" else "blocked",
        "trade_date": trade_date.isoformat(), "provider_requests": 0, "close_snapshot": snapshot,
        "limit_up_pool": {"source": pool_source, "rows": int(pool.get("rows") or 0),
                          "symbols": int(pool.get("symbols") or 0), "snapshots": int(pool.get("snapshots") or 0),
                          "first_observed_at": _iso(pool.get("first_observed_at")),
                          "last_observed_at": _iso(pool.get("last_observed_at"))},
        "limit_chain": {"source": ladder_source, "rows": int(ladder.get("rows") or 0),
                        "symbols": int(ladder.get("symbols") or 0),
                        "last_observed_at": _iso(ladder.get("last_observed_at"))},
        "concept_strength": {"status": "unavailable",
                             "reason": "no pattern-mining reader uses concept limit strength; "
                                       "the retired limit_cpt_list is not derived here"},
    }
    if report["status"] == "blocked":
        report["reason"] = snapshot["reason"]
    return report


__all__ = [
    "CLOSE_SNAPSHOT_FROM", "CloseLimitEvents", "PRIOR_SESSION_LOOKBACK", "close_snapshot", "limit_event_evidence",
    "load_close_limit_events", "load_close_limit_events_async", "load_prior_close_pool", "session_window",
]
