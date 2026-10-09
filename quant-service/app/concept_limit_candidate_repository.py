"""Local reads and writes for THS concept limit-up candidates.

This module owns no provider request, scheduler or strategy score.  It reads
the session's Fuyao limit-up pool already captured into
``quant.market_events`` and the ``fuyao_ths_concept`` membership already
stored, and writes the candidate rows; the caller keeps all of it inside the
bounded DB executor.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from psycopg.types.json import Json

from .concept_limit_strength import event_body, limit_tag
from .datasources.http import number
from .sector_membership_repository import point_in_time_membership_predicate, sector_group_predicate


TAXONOMY_KEY = "fuyao_ths_concept"
PROVIDER_KEY = "fuyao_ths"
_CN_TZ = ZoneInfo("Asia/Shanghai")
_POOL = "event_type='limit_up_pool' AND source='fuyao_ths'"


def latest_limit_up_pool(
    database: Any, requested_date: date | None,
) -> tuple[date | None, datetime | None, dict[str, dict[str, Any]]]:
    """The last pool snapshot captured on the session, keyed by symbol.

    The capture stores one row per member per minute; the newest minute of
    the day is the pool as the session ended (or as it stands, before the
    close).  Without a date the newest captured session is used.
    """
    with database.transaction() as connection:
        selected = requested_date
        if selected is None:
            latest = connection.execute(f"SELECT max(occurred_at) at FROM quant.market_events WHERE {_POOL}").fetchone()
            if not latest or latest["at"] is None:
                return None, None, {}
            selected = latest["at"].astimezone(_CN_TZ).date()
        start = datetime.combine(selected, time(0, 0), tzinfo=_CN_TZ)
        snapshot = connection.execute(
            f"SELECT max(occurred_at) at FROM quant.market_events WHERE {_POOL} AND occurred_at>=%s AND occurred_at<%s",
            (start, start + timedelta(days=1)),
        ).fetchone()
        snapshot_at = snapshot["at"] if snapshot else None
        if snapshot_at is None:
            return selected, None, {}
        rows = connection.execute(
            f"SELECT symbol,body FROM quant.market_events WHERE {_POOL} AND occurred_at=%s",
            (snapshot_at,),
        ).fetchall()
    return selected, snapshot_at, {str(row["symbol"]).upper(): event_body(row) for row in rows if row["symbol"]}


def concept_memberships(
    database: Any, trade_date: date, symbols: list[str],
) -> tuple[list[dict[str, Any]], dict[str, int], dict[str, str]]:
    """Point-in-time concept relations of ``symbols``, sectors only.

    Qualification lists (融资融券, index samples, report-period lists) are
    excluded as everywhere a shared group is read as a sector; otherwise
    融资融券 would be the "strongest concept" of every session.
    """
    membership_predicate = point_in_time_membership_predicate("member")
    sector_only, sector_parameters = sector_group_predicate("member")
    with database.transaction() as connection:
        rows = connection.execute(
            f"""SELECT DISTINCT member.sector_key,member.symbol FROM quant.sector_membership_history member
                 WHERE member.taxonomy_key=%s AND member.symbol=ANY(%s) AND {membership_predicate} AND {sector_only}""",
            (TAXONOMY_KEY, symbols, trade_date, trade_date, trade_date, *sector_parameters),
        ).fetchall()
        sector_keys = sorted({str(row["sector_key"]) for row in rows})
        if not sector_keys:
            return [], {}, {}
        counts = connection.execute(
            f"""SELECT member.sector_key,count(DISTINCT member.symbol)::int members
                  FROM quant.sector_membership_history member
                 WHERE member.taxonomy_key=%s AND member.sector_key=ANY(%s) AND {membership_predicate}
                 GROUP BY member.sector_key""",
            (TAXONOMY_KEY, sector_keys, trade_date, trade_date, trade_date),
        ).fetchall()
        labels = connection.execute(
            "SELECT sector_key,label FROM quant.sectors WHERE taxonomy_key=%s AND sector_key=ANY(%s)",
            (TAXONOMY_KEY, sector_keys),
        ).fetchall()
    return ([dict(row) for row in rows], {str(row["sector_key"]): int(row["members"] or 0) for row in counts},
            {str(row["sector_key"]): str(row["label"]) for row in labels})


def persist_candidates(
    database: Any,
    trade_date: date,
    snapshot_at: datetime,
    concepts: list[dict[str, Any]],
    pool: dict[str, dict[str, Any]],
    leaders_per_concept: int,
    observed_at: datetime,
) -> tuple[int, list[dict[str, Any]]]:
    """Replace the session's candidates with each concept's leading members.

    The whole session is replaced, so a concept that dropped out of the top
    since an earlier run leaves no stale rows behind.
    """
    rows: list[tuple[Any, ...]] = []
    per_concept: list[dict[str, Any]] = []
    for concept in concepts:
        selected = concept["limit_up_symbols"][:leaders_per_concept]
        strength = {key: concept[key] for key in ("rank", "limit_up_count", "member_count", "limit_up_ratio", "max_board_count")}
        for symbol in selected:
            item = pool[symbol]
            rows.append((
                TAXONOMY_KEY, concept["sector_key"], symbol, trade_date, PROVIDER_KEY, observed_at,
                item.get("name"), limit_tag(item), "涨停池", number(item.get("max_seal_money")),
                item.get("limit_up_reason"),
                Json({"limit_up_pool": item, "snapshot_at": snapshot_at.isoformat(), "concept": strength,
                      "membership_taxonomy_key": TAXONOMY_KEY, "membership_fetch_status": "completed",
                      "membership_basis": "point_in_time_stored_snapshot"}),
            ))
        per_concept.append({"sector_key": concept["sector_key"], "label": concept["label"], **strength,
                            "matched_limit_ups": concept["limit_up_count"], "stored": len(selected)})
    with database.transaction() as connection:
        connection.execute(
            "DELETE FROM quant.sector_limit_candidates WHERE taxonomy_key=%s AND trading_date=%s AND provider_key=%s",
            (TAXONOMY_KEY, trade_date, PROVIDER_KEY),
        )
        if rows:
            with connection.cursor() as cursor:
                cursor.executemany(
                    """INSERT INTO quant.sector_limit_candidates(taxonomy_key,sector_key,symbol,trading_date,provider_key,
                             available_at,name,limit_tag,limit_type,limit_amount,status,description,raw)
                       VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'涨停',%s,%s)""",
                    rows,
                )
    return len(rows), per_concept


__all__ = ["PROVIDER_KEY", "TAXONOMY_KEY", "concept_memberships", "latest_limit_up_pool", "persist_candidates"]
