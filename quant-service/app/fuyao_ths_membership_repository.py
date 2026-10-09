"""Local state for the Fuyao THS board catalogue and member refresh.

The refresh owns the provider calls; this module owns the rows they leave
behind.  A board's catalogue row records the date Fuyao last listed it
(``metadata.listed_on``) and ``quant.sector_member_sync_state`` holds one
receipt per board and refresh date, so a restart, a second caller or the
next batch resumes from what is durable instead of starting over, and a
board Fuyao no longer lists is simply not refreshed again.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Iterable, Sequence

from psycopg.types.json import Json

from .sector_catalog_repository import upsert_sectors
from .sector_membership_repository import observed_exchange_date, persist_observed_snapshot_delta


PROVIDER_KEY = "fuyao_ths"
#: Failed boards are retried within the day up to this many attempts; a
#: board that keeps failing must not hold every later batch on itself.
MAX_FAILED_ATTEMPTS = 3

_LISTED_SECTORS = "s.taxonomy_key=%s AND s.metadata->>'listed_on'=%s"
_SYNC_STATE_UPSERT = """
    INSERT INTO quant.sector_member_sync_state(
        taxonomy_key,sector_key,trading_date,state,attempts,member_count,last_error,provider_key,updated_at)
    VALUES(%s,%s,%s,%s,1,%s,%s,%s,now())
    ON CONFLICT(taxonomy_key,sector_key,trading_date) DO UPDATE SET state=EXCLUDED.state,
      attempts=quant.sector_member_sync_state.attempts+1,member_count=EXCLUDED.member_count,
      last_error=EXCLUDED.last_error,provider_key=EXCLUDED.provider_key,updated_at=now()
"""


def persist_catalog(database: Any, taxonomy_key: str, label: str, tag: str,
                    boards: Iterable[tuple[str, str]], listed_on: date) -> int:
    """Store one tag's listing; every listed board is stamped with the date."""
    rows = [(code, name, {"source": "fuyao:ths_index_list", "tag": tag, "listed_on": listed_on.isoformat()})
            for code, name in boards]
    with database.transaction() as connection:
        connection.execute(
            """INSERT INTO quant.sector_taxonomies(taxonomy_key,label,provider_key,metadata)
               VALUES(%s,%s,%s,%s)
               ON CONFLICT(taxonomy_key) DO UPDATE SET label=EXCLUDED.label,
                 provider_key=EXCLUDED.provider_key,metadata=EXCLUDED.metadata,updated_at=now()""",
            (taxonomy_key, label, PROVIDER_KEY,
             Json({"route": "ths_index_constituents", "catalog_route": "ths_index_list", "tag": tag})),
        )
        return upsert_sectors(connection, taxonomy_key, rows)


def listed_counts(database: Any, taxonomy_keys: Sequence[str], listed_on: date) -> dict[str, int]:
    """Boards each taxonomy's catalogue listed on ``listed_on``."""
    with database.transaction() as connection:
        rows = connection.execute(
            """SELECT taxonomy_key,count(*)::int listed FROM quant.sectors
                WHERE taxonomy_key=ANY(%s) AND metadata->>'listed_on'=%s GROUP BY taxonomy_key""",
            (list(taxonomy_keys), listed_on.isoformat()),
        ).fetchall()
    return {str(row["taxonomy_key"]): int(row["listed"] or 0) for row in rows}


def boards_due(database: Any, taxonomy_key: str, refresh_date: date, limit: int) -> tuple[list[dict[str, Any]], int]:
    """Listed boards with no settled receipt for ``refresh_date``, in code order.

    ``completed`` and ``empty`` settle a board for the day; a ``failed`` one
    is due again until it has used its attempts.
    """
    with database.transaction() as connection:
        rows = connection.execute(
            f"""SELECT s.sector_key,s.label FROM quant.sectors s
                  LEFT JOIN quant.sector_member_sync_state state
                    ON state.taxonomy_key=s.taxonomy_key AND state.sector_key=s.sector_key AND state.trading_date=%s
                 WHERE {_LISTED_SECTORS}
                   AND (state.state IS NULL OR (state.state='failed' AND state.attempts<%s))
                 ORDER BY s.sector_key LIMIT %s""",
            (refresh_date, taxonomy_key, refresh_date.isoformat(), MAX_FAILED_ATTEMPTS, max(0, int(limit))),
        ).fetchall()
        total = connection.execute(
            f"SELECT count(*)::int total FROM quant.sectors s WHERE {_LISTED_SECTORS}",
            (taxonomy_key, refresh_date.isoformat()),
        ).fetchone()
    return [dict(row) for row in rows], int((total or {}).get("total") or 0)


def boards_page(database: Any, taxonomy_key: str, listed_on: date, offset: int, limit: int) -> tuple[list[dict[str, Any]], int]:
    """An explicit offset page of the day's listing, for manual batches."""
    with database.transaction() as connection:
        rows = connection.execute(
            f"""SELECT s.sector_key,s.label FROM quant.sectors s WHERE {_LISTED_SECTORS}
                 ORDER BY s.sector_key LIMIT %s OFFSET %s""",
            (taxonomy_key, listed_on.isoformat(), max(0, int(limit)), max(0, int(offset))),
        ).fetchall()
        total = connection.execute(
            f"SELECT count(*)::int total FROM quant.sectors s WHERE {_LISTED_SECTORS}",
            (taxonomy_key, listed_on.isoformat()),
        ).fetchone()
    return [dict(row) for row in rows], int((total or {}).get("total") or 0)


def persist_member_snapshot(database: Any, taxonomy_key: str, sector_key: str,
                            members: dict[str, dict[str, Any]], observed_at: datetime) -> dict[str, Any]:
    """Apply one complete constituent response and settle the board's receipt."""
    with database.transaction() as connection:
        delta = persist_observed_snapshot_delta(
            connection, taxonomy_key, sector_key, members, PROVIDER_KEY, observed_at,
            instrument_source=PROVIDER_KEY,
        )
        state = "completed" if members else "empty"
        connection.execute(_SYNC_STATE_UPSERT, (
            taxonomy_key, sector_key, observed_exchange_date(observed_at), state, len(members), None, PROVIDER_KEY,
        ))
    return {**delta, "state": state}


def record_member_failure(database: Any, taxonomy_key: str, sector_key: str, refresh_date: date, detail: str) -> None:
    """Count a failed attempt without touching the board's open members."""
    with database.transaction() as connection:
        connection.execute(_SYNC_STATE_UPSERT, (
            taxonomy_key, sector_key, refresh_date, "failed", 0, detail[:500], PROVIDER_KEY,
        ))


def refresh_progress(database: Any, taxonomy_keys: Sequence[str], refresh_date: date) -> dict[str, dict[str, int]]:
    """Per taxonomy: boards listed for the date, settled, failing and still due."""
    with database.transaction() as connection:
        rows = connection.execute(
            """SELECT s.taxonomy_key,count(*)::int listed,
                      count(*) FILTER (WHERE state.state IN ('completed','empty'))::int done,
                      count(*) FILTER (WHERE state.state='failed')::int failed,
                      count(*) FILTER (WHERE state.state='failed' AND state.attempts>=%s)::int exhausted
                 FROM quant.sectors s
                 LEFT JOIN quant.sector_member_sync_state state
                   ON state.taxonomy_key=s.taxonomy_key AND state.sector_key=s.sector_key AND state.trading_date=%s
                WHERE s.taxonomy_key=ANY(%s) AND s.metadata->>'listed_on'=%s
                GROUP BY s.taxonomy_key""",
            (MAX_FAILED_ATTEMPTS, refresh_date, list(taxonomy_keys), refresh_date.isoformat()),
        ).fetchall()
    progress = {key: {"listed": 0, "completed_or_empty": 0, "failed": 0, "remaining": 0} for key in taxonomy_keys}
    for row in rows:
        listed, done = int(row["listed"] or 0), int(row["done"] or 0)
        progress[str(row["taxonomy_key"])] = {
            "listed": listed, "completed_or_empty": done, "failed": int(row["failed"] or 0),
            "remaining": max(0, listed - done - int(row["exhausted"] or 0)),
        }
    return progress


__all__ = [
    "MAX_FAILED_ATTEMPTS", "PROVIDER_KEY", "boards_due", "boards_page", "listed_counts", "persist_catalog",
    "persist_member_snapshot", "record_member_failure", "refresh_progress",
]
