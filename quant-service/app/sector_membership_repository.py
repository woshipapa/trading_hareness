"""Point-in-time sector-membership persistence and read predicates.

Provider constituent responses often describe the current snapshot but omit a
historical ``in_date``.  Treating that omission as 1900-01-01 makes a current
board composition appear to have been known throughout history.  This module
keeps that distinction explicit: supplier intervals and observed snapshots
have different bases, and every read is bounded by when the snapshot became
known.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Callable
from zoneinfo import ZoneInfo

from psycopg.types.json import Json

from .datasources.catalog import NON_SECTOR_GROUPS, NON_SECTOR_LABEL_PATTERN
from .instrument_registry import InstrumentRecord, ensure_instruments


PROVIDER_INTERVAL = "provider_interval"
OBSERVED_SNAPSHOT = "observed_snapshot"
LEGACY_UNBOUNDED = "legacy_unbounded"


def observed_exchange_date(observed_at: datetime) -> date:
    """Return the Shanghai exchange date on which a snapshot became known."""
    return observed_at.astimezone(ZoneInfo("Asia/Shanghai")).date()


def membership_interval(
    row: dict[str, Any],
    observed_at: datetime,
    *,
    parse_date: Callable[[Any], date | None],
) -> tuple[date, date | None, str, str]:
    """Derive a non-fictional interval and preserve its provenance basis."""
    effective_from = parse_date(row.get("in_date"))
    effective_to = parse_date(row.get("out_date"))
    if effective_from is not None:
        return effective_from, effective_to, PROVIDER_INTERVAL, PROVIDER_INTERVAL
    return observed_exchange_date(observed_at), effective_to, OBSERVED_SNAPSHOT, (
        PROVIDER_INTERVAL if effective_to is not None else OBSERVED_SNAPSHOT
    )


def point_in_time_membership_predicate(
    alias: str = "member",
    date_parameter: str = "%s",
    known_at_cutoff_sql: str | None = None,
) -> str:
    """Return a strict predicate for as-known-at sector membership joins.

    A later refresh may describe an older provider interval, but it must not
    be visible to a replay before that refresh was actually received.  Legacy
    rows with the old synthetic 1900 start date remain auditable but cannot
    silently enter research features.
    """
    cutoff = known_at_cutoff_sql or f"(({date_parameter}::date + time '17:00:00') AT TIME ZONE 'Asia/Shanghai')"
    return (
        f"{alias}.effective_from<={date_parameter} AND "
        f"({alias}.effective_to IS NULL OR {alias}.effective_to>={date_parameter}) AND "
        f"{alias}.effective_from_basis IN ('{PROVIDER_INTERVAL}','{OBSERVED_SNAPSHOT}') AND "
        f"{alias}.known_at <= {cutoff}"
    )


def sector_group_predicate(alias: str = "member") -> tuple[str, tuple[Any, ...]]:
    """SQL (and its parameters) that keeps only groups that are sectors.

    Drops the qualification lists the catalog names -- 融资融券, index
    constituents, reporting-period lists -- by key, and by label for the ones
    THS mints later.  Use it wherever a shared group is read as "these names
    move together": peer sets and sector exposure.
    """
    return (
        f"NOT ({alias}.sector_key = ANY(%s)) AND NOT EXISTS ("
        f"SELECT 1 FROM quant.sectors non_sector WHERE non_sector.taxonomy_key={alias}.taxonomy_key "
        f"AND non_sector.sector_key={alias}.sector_key AND non_sector.label ~ %s)",
        (list(NON_SECTOR_GROUPS), NON_SECTOR_LABEL_PATTERN),
    )


# ``known_at`` is the point-in-time boundary replays filter on.  A refresh or
# an interval close must never move it later: that hid a membership from every
# replay dated before the latest refresh.  Upserts keep the earliest value and
# closes leave it untouched; ``available_at`` still records the latest write.


def persist_ths_snapshot(
    connection: Any,
    taxonomy_key: str,
    sector_key: str,
    rows: list[dict[str, Any]],
    provider_key: str,
    observed_at: datetime,
    *,
    ensure_instrument: Callable[[Any, str], None],
    parse_date: Callable[[Any], date | None],
) -> int:
    """Store one complete THS constituent response with explicit time basis."""
    active_members: set[str] = set()
    instrument_rows: list[InstrumentRecord] = []
    parsed_rows: list[tuple[dict[str, Any], str, date, date | None, str, str]] = []
    for row in rows:
        symbol = str(row.get("con_code") or "").upper()
        if len(symbol) != 9 or symbol[6:] not in {".SH", ".SZ", ".BJ"} or not symbol[:6].isdigit():
            continue
        effective_from, effective_to, from_basis, to_basis = membership_interval(
            row, observed_at, parse_date=parse_date,
        )
        instrument_rows.append(InstrumentRecord(
            symbol=symbol, exchange=symbol.rsplit(".", 1)[-1], source=provider_key,
        ))
        parsed_rows.append((row, symbol, effective_from, effective_to, from_basis, to_basis))
    ensure_instruments(connection, instrument_rows, source=provider_key)
    for row, symbol, effective_from, effective_to, from_basis, to_basis in parsed_rows:
        connection.execute(
            """INSERT INTO quant.sector_membership_history(
                   taxonomy_key,sector_key,symbol,effective_from,effective_to,provider_key,
                   available_at,known_at,effective_from_basis,effective_to_basis,raw
               ) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT(taxonomy_key,sector_key,symbol,effective_from) DO UPDATE
                 SET effective_to=EXCLUDED.effective_to,provider_key=EXCLUDED.provider_key,
                     available_at=EXCLUDED.available_at,
                     known_at=LEAST(sector_membership_history.known_at,EXCLUDED.known_at),
                     effective_from_basis=EXCLUDED.effective_from_basis,
                     effective_to_basis=EXCLUDED.effective_to_basis,raw=EXCLUDED.raw""",
            (taxonomy_key, sector_key, symbol, effective_from, effective_to, provider_key,
             observed_at, observed_at, from_basis, to_basis, Json(row)),
        )
        if effective_to is None:
            active_members.add(symbol)
    if rows:
        connection.execute(
            """UPDATE quant.sector_membership_history
                  SET effective_to=%s,available_at=%s,effective_to_basis=%s
                WHERE taxonomy_key=%s AND sector_key=%s AND provider_key=%s AND effective_to IS NULL
                  AND NOT symbol = ANY(%s)""",
            (observed_exchange_date(observed_at) - timedelta(days=1), observed_at,
             OBSERVED_SNAPSHOT, taxonomy_key, sector_key, provider_key, list(active_members)),
        )
    return len(active_members)


def persist_observed_snapshot(
    connection: Any,
    taxonomy_key: str,
    sector_key: str,
    rows: list[dict[str, Any]],
    provider_key: str,
    observed_at: datetime,
    *,
    member_symbol: Callable[[dict[str, Any]], str | None],
    ensure_instrument: Callable[[Any, str, dict[str, Any]], None],
) -> int:
    """Store a provider snapshot that has no historical membership interval."""
    members: set[str] = set()
    stored = 0
    effective_from = observed_exchange_date(observed_at)
    parsed_rows: list[tuple[dict[str, Any], str]] = []
    for row in rows:
        symbol = member_symbol(row)
        if not symbol:
            continue
        parsed_rows.append((row, symbol))
    ensure_instruments(
        connection,
        [InstrumentRecord(
            symbol=symbol, exchange=symbol.rsplit(".", 1)[-1],
            name=str(row.get("名称") or row.get("name") or "").strip() or None,
            source=provider_key,
        ) for row, symbol in parsed_rows],
        source=provider_key, update_existing=True,
    )
    for row, symbol in parsed_rows:
        connection.execute(
            """INSERT INTO quant.sector_membership_history(
                   taxonomy_key,sector_key,symbol,effective_from,effective_to,provider_key,
                   available_at,known_at,effective_from_basis,effective_to_basis,raw
               ) VALUES(%s,%s,%s,%s,NULL,%s,%s,%s,%s,%s,%s)
               ON CONFLICT(taxonomy_key,sector_key,symbol,effective_from) DO UPDATE
                 SET effective_to=NULL,provider_key=EXCLUDED.provider_key,
                     available_at=EXCLUDED.available_at,
                     known_at=LEAST(sector_membership_history.known_at,EXCLUDED.known_at),
                     effective_from_basis=EXCLUDED.effective_from_basis,
                     effective_to_basis=EXCLUDED.effective_to_basis,raw=EXCLUDED.raw""",
            (taxonomy_key, sector_key, symbol, effective_from, provider_key, observed_at, observed_at,
             OBSERVED_SNAPSHOT, OBSERVED_SNAPSHOT, Json(row)),
        )
        members.add(symbol)
        stored += 1
    if rows:
        connection.execute(
            """UPDATE quant.sector_membership_history
                  SET effective_to=%s,available_at=%s,effective_to_basis=%s
                WHERE taxonomy_key=%s AND sector_key=%s AND provider_key=%s AND effective_to IS NULL
                  AND effective_from<%s AND NOT symbol = ANY(%s)""",
            (effective_from - timedelta(days=1), observed_at, OBSERVED_SNAPSHOT,
             taxonomy_key, sector_key, provider_key, effective_from, list(members)),
        )
    return stored


def persist_observed_snapshot_batched(
    connection: Any,
    taxonomy_key: str,
    sector_key: str,
    members: dict[str, dict[str, Any]],
    provider_key: str,
    observed_at: datetime,
    *,
    instrument_source: str,
) -> int:
    """:func:`persist_observed_snapshot` in three round trips per sector.

    Same statements and interval semantics, sent with ``executemany``: over
    the 52ms owner tunnel the per-member form costs two round trips per
    member, which is hours for a full concept taxonomy.  ``members`` maps a
    symbol to its raw provider row (``name`` feeds the instrument label).
    """
    if not members:
        return 0
    effective_from = observed_exchange_date(observed_at)
    ensure_instruments(
        connection,
        [InstrumentRecord(
            symbol=symbol, exchange=symbol.rsplit(".", 1)[-1],
            name=str(row.get("name") or "").strip() or None,
            source=instrument_source,
        ) for symbol, row in members.items()],
        source=instrument_source,
    )
    with connection.cursor() as cursor:
        cursor.executemany(
            """INSERT INTO quant.sector_membership_history(
                   taxonomy_key,sector_key,symbol,effective_from,effective_to,provider_key,
                   available_at,known_at,effective_from_basis,effective_to_basis,raw
               ) VALUES(%s,%s,%s,%s,NULL,%s,%s,%s,%s,%s,%s)
               ON CONFLICT(taxonomy_key,sector_key,symbol,effective_from) DO UPDATE
                 SET effective_to=NULL,provider_key=EXCLUDED.provider_key,
                     available_at=EXCLUDED.available_at,
                     known_at=LEAST(sector_membership_history.known_at,EXCLUDED.known_at),
                     effective_from_basis=EXCLUDED.effective_from_basis,
                     effective_to_basis=EXCLUDED.effective_to_basis,raw=EXCLUDED.raw""",
            [(taxonomy_key, sector_key, symbol, effective_from, provider_key, observed_at, observed_at,
              OBSERVED_SNAPSHOT, OBSERVED_SNAPSHOT, Json(row)) for symbol, row in members.items()],
        )
    connection.execute(
        """UPDATE quant.sector_membership_history
              SET effective_to=%s,available_at=%s,effective_to_basis=%s
            WHERE taxonomy_key=%s AND sector_key=%s AND provider_key=%s AND effective_to IS NULL
              AND effective_from<%s AND NOT symbol = ANY(%s)""",
        (effective_from - timedelta(days=1), observed_at, OBSERVED_SNAPSHOT,
         taxonomy_key, sector_key, provider_key, effective_from, list(members)),
    )
    return len(members)


__all__ = [
    "LEGACY_UNBOUNDED", "OBSERVED_SNAPSHOT", "PROVIDER_INTERVAL", "membership_interval",
    "observed_exchange_date", "persist_observed_snapshot", "persist_observed_snapshot_batched",
    "persist_ths_snapshot", "point_in_time_membership_predicate", "sector_group_predicate",
]
