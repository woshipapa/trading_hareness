"""Deterministic, lock-safe instrument registry writes.

The owner PostgreSQL instance is reached through an SSH tunnel.  A row-at-a-
time ``INSERT ... ON CONFLICT`` therefore turns a harmless cross-section into
thousands of network round trips and gives concurrent writers different lock
orders.  All high-volume callers use this module so symbols are deduplicated,
sorted and written by one ``unnest`` statement.

This module contains no provider or HTTP code.  It is safe to reuse from
offline imports, owner-side collectors and research-only writers.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date
from typing import Any, Iterable, Mapping

from .instrument_lock_retry import run_instrument_write_with_retry


@dataclass(frozen=True)
class InstrumentRecord:
    symbol: str
    exchange: str
    name: str | None = None
    industry: str | None = None
    list_date: date | None = None
    delist_date: date | None = None
    is_st: bool | None = None
    source: str = "manual"


def exchange_for(symbol: str) -> str:
    return symbol.rsplit(".", 1)[-1]


def _text(value: Any) -> str | None:
    if value in (None, ""):
        return None
    return str(value)


def record_from(value: InstrumentRecord | Mapping[str, Any] | str, *, source: str = "manual",
                exchange: str | None = None) -> InstrumentRecord:
    """Normalize one caller row without guessing a missing symbol."""
    if isinstance(value, InstrumentRecord):
        return value
    if isinstance(value, str):
        symbol = value.strip().upper()
        return InstrumentRecord(symbol=symbol, exchange=exchange or exchange_for(symbol), source=source)
    symbol = str(value.get("symbol") or value.get("ts_code") or "").strip().upper()
    if not symbol:
        raise ValueError("instrument row has no symbol")
    return InstrumentRecord(
        symbol=symbol,
        exchange=str(value.get("exchange") or exchange or exchange_for(symbol)).strip().upper(),
        name=_text(value.get("name")),
        industry=_text(value.get("industry")),
        list_date=value.get("list_date"),
        delist_date=value.get("delist_date"),
        is_st=value.get("is_st"),
        source=str(value.get("source") or source),
    )


def sorted_instrument_records(values: Iterable[InstrumentRecord | Mapping[str, Any] | str], *,
                              source: str = "manual") -> list[InstrumentRecord]:
    """Return one deterministic row per symbol.

    If multiple producers mention a symbol in one batch, the first non-null
    metadata in sorted input order wins.  This is deliberately deterministic
    and keeps every writer's lock order identical.
    """
    merged: dict[str, InstrumentRecord] = {}
    for raw in values:
        row = record_from(raw, source=source)
        current = merged.get(row.symbol)
        if current is None:
            merged[row.symbol] = row
            continue
        merged[row.symbol] = replace(
            current,
            exchange=current.exchange or row.exchange,
            name=current.name or row.name,
            industry=current.industry or row.industry,
            list_date=current.list_date or row.list_date,
            delist_date=current.delist_date or row.delist_date,
            is_st=current.is_st if current.is_st is not None else row.is_st,
            source=current.source or row.source,
        )
    return [merged[key] for key in sorted(merged)]


def ensure_instruments(connection: Any, values: Iterable[InstrumentRecord | Mapping[str, Any] | str], *,
                       source: str = "manual", update_existing: bool = False) -> int:
    """Upsert instruments in one sorted ``unnest`` statement.

    ``update_existing=False`` is the conservative placeholder mode used by
    readers and evidence importers.  Metadata-bearing owner syncs may opt into
    ``update_existing=True``; even then fields are only replaced when the
    incoming value is non-null, so a sparse quote row cannot erase stock_basic
    metadata.
    """
    rows = sorted_instrument_records(values, source=source)
    if not rows:
        return 0
    # ``quant.instruments.is_st`` is NOT NULL on older owner schemas.  A
    # sparse quote row therefore cannot be inserted with NULL merely to make
    # the conflict clause preserve an existing flag.  Insert those rows in the
    # conservative placeholder mode (false only for a genuinely new symbol)
    # and reserve the metadata-updating statement for rows that carry a real
    # flag.  This keeps the old schema safe while retaining the new batch path.
    if update_existing and any(row.is_st is None for row in rows):
        # New rows cannot insert NULL into the legacy NOT NULL flag column,
        # while existing rows must preserve a real flag when the incoming
        # record is sparse.  Partition only into *contiguous* runs of the
        # already symbol-sorted list.  The old implementation wrote all
        # sparse rows first and all flagged rows second, which could acquire
        # locks in opposite orders from another writer and recreate the
        # owner-side ON CONFLICT deadlock.
        runs: list[tuple[bool, list[InstrumentRecord]]] = []
        for row in rows:
            sparse = row.is_st is None
            if not runs or runs[-1][0] != sparse:
                runs.append((sparse, []))
            runs[-1][1].append(row)
        written = 0
        for sparse, run in runs:
            values = [replace(row, is_st=False) for row in run] if sparse else run
            written += ensure_instruments(
                connection, values, source=source, update_existing=not sparse,
            )
        return written
    update = """
        exchange=COALESCE(EXCLUDED.exchange,quant.instruments.exchange),
        name=COALESCE(EXCLUDED.name,quant.instruments.name),
        industry=COALESCE(EXCLUDED.industry,quant.instruments.industry),
        list_date=COALESCE(EXCLUDED.list_date,quant.instruments.list_date),
        delist_date=COALESCE(EXCLUDED.delist_date,quant.instruments.delist_date),
        is_st=COALESCE(EXCLUDED.is_st,quant.instruments.is_st),
        source=COALESCE(NULLIF(EXCLUDED.source,''),quant.instruments.source),
        updated_at=now()
    """ if update_existing else ""
    conflict = f"DO UPDATE SET {update}" if update_existing else "DO NOTHING"
    statement = f"""INSERT INTO quant.instruments(
                  symbol,exchange,name,industry,list_date,delist_date,is_st,source)
               SELECT symbol,exchange,name,industry,list_date,delist_date,COALESCE(is_st,false),source
                 FROM unnest(
                    %s::text[],%s::text[],%s::text[],%s::text[],
                    %s::date[],%s::date[],%s::boolean[],%s::text[]
                 ) AS incoming(symbol,exchange,name,industry,list_date,delist_date,is_st,source)
                ORDER BY symbol
               ON CONFLICT(symbol) {conflict}"""
    parameters = (
            [row.symbol for row in rows], [row.exchange for row in rows],
            [row.name for row in rows], [row.industry for row in rows],
            [row.list_date for row in rows], [row.delist_date for row in rows],
            [row.is_st for row in rows], [row.source for row in rows],
        )
    def write() -> None:
        if hasattr(connection, "execute"):
            connection.execute(statement, parameters)
        else:
            # Some repository contract fakes, and older psycopg connection
            # wrappers, expose only a cursor. One executemany item retains the
            # same one-statement/one-batch semantics.
            with connection.cursor() as cursor:
                cursor.executemany(statement, [parameters])

    run_instrument_write_with_retry(connection, write)
    return len(rows)


def ensure_instrument(connection: Any, symbol: str, *, source: str = "manual",
                      name: str | None = None, update_existing: bool = False) -> None:
    """Compatibility wrapper for low-volume callers; it still uses the batch path."""
    ensure_instruments(
        connection,
        [InstrumentRecord(symbol=symbol.strip().upper(), exchange=exchange_for(symbol), name=name, source=source)],
        update_existing=update_existing,
    )


__all__ = [
    "InstrumentRecord", "ensure_instrument", "ensure_instruments", "exchange_for",
    "record_from", "sorted_instrument_records",
]
