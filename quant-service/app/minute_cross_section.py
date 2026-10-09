"""The all-A minute cross-section, one document a minute (decision 0009).

The level1 capture used to write each minute as 5,558 rows of
``raw_market_observations`` (``a_share_prices_snapshot``). On 2026-10-09 that
was 93% of a 35 GB table. Every row repeated the minute's metadata and held
the same JSON in two columns, and each minute's write took 5-53 s on the
owner. Here a minute is one row, ``a_share_minute_cross_section``, whose
``normalized`` column is a lossless columnar document:

- ``constants``: fields equal in every row, such as the minute's metadata,
  stored once;
- ``columns``: the varying fields, one list each. A string column with few
  distinct values (price timestamps, price sources) is dictionary-encoded;
- ``aliases``: a column identical to another (``ts_code`` and ``symbol``,
  ``raw.turnover`` and ``turnover``), stored as a reference;
- ``missing``: the rows a field was absent from.

``rows_of`` rebuilds the original row dicts exactly, so readers that used the
per-symbol rows see the same shape. Days before the switch still read the
per-symbol rows through the legacy fallbacks here. The newest minute is also
kept in this process's memory for the latest-snapshot read.

No DDL: the documents live in the existing table under their own capability,
so the hot/cold tiering and the overflow archive apply as they did.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from . import settings

SCHEMA = "minute-cross-section-v1"
PROVIDER_KEY = "fuyao_ths"
CAPABILITY = "a_share_minute_cross_section"
LEGACY_CAPABILITY = "a_share_prices_snapshot"
CN_TZ = ZoneInfo("Asia/Shanghai")
#: Dict-valued fields flattened one level when they vary by row.
NESTED = ("raw",)
#: A string column is dictionary-encoded when it has at most this share of distinct values.
DICTIONARY_SHARE = 0.25
STORAGE_MODES = ("both", "document", "per_symbol")
#: How old the in-memory minute may be and still answer "latest".
MEMORY_MAX_AGE = timedelta(seconds=150)


def storage_mode() -> str:
    mode = settings.text("LEVEL1_STORAGE").lower() or "both"
    return mode if mode in STORAGE_MODES else "both"


# -- the document -------------------------------------------------------------

def _encode(column: list[Any]) -> dict[str, Any]:
    if all(value is None or isinstance(value, str) for value in column):
        distinct = list(dict.fromkeys(column))
        if len(distinct) <= max(16, int(len(column) * DICTIONARY_SHARE)):
            index = {value: position for position, value in enumerate(distinct)}
            return {"dict": distinct, "codes": [index[value] for value in column]}
    return {"values": column}


def _decode(entry: Mapping[str, Any]) -> list[Any]:
    if "dict" in entry:
        dictionary = entry["dict"]
        return [dictionary[code] for code in entry["codes"]]
    return list(entry["values"])


_ABSENT = object()


def build_document(rows: Sequence[Mapping[str, Any]], observed_at: datetime,
                   metadata: Mapping[str, Any] | None = None) -> dict[str, Any]:
    count = len(rows)
    columns: dict[str, list[Any]] = {}
    order: list[str] = []
    for position, row in enumerate(rows):
        for key, value in row.items():
            pairs = (((f"{key}.{sub}", item) for sub, item in value.items())
                     if key in NESTED and isinstance(value, Mapping) else ((key, value),))
            for path, item in pairs:
                column = columns.get(path)
                if column is None:
                    column = columns[path] = [_ABSENT] * count
                    order.append(path)
                column[position] = item
    constants: dict[str, Any] = {}
    encoded: dict[str, Any] = {}
    aliases: dict[str, str] = {}
    missing: dict[str, list[int]] = {}
    seen: dict[str, str] = {}
    for path in order:
        column = columns[path]
        absent = [position for position, value in enumerate(column) if value is _ABSENT]
        if absent:
            missing[path] = absent
            column = [None if value is _ABSENT else value for value in column]
        first = column[0] if column else None
        if count and all(value == first for value in column) and not absent:
            constants[path] = first
            continue
        fingerprint = json.dumps(column, sort_keys=True, default=str, ensure_ascii=False)
        if fingerprint in seen:
            aliases[path] = seen[fingerprint]
            continue
        seen[fingerprint] = path
        encoded[path] = _encode(column)
    return {
        "schema": SCHEMA, "observed_at": observed_at.isoformat(), "rows": count, "order": order,
        "constants": constants, "columns": encoded, "aliases": aliases, "missing": missing,
        "metadata": dict(metadata or {}),
    }


def rows_of(document: Mapping[str, Any], indices: Sequence[int] | None = None) -> list[dict[str, Any]]:
    """The original rows, in capture order (or for ``indices``)."""
    count = int(document.get("rows") or 0)
    values = {path: _decode(entry) for path, entry in (document.get("columns") or {}).items()}
    for alias, target in (document.get("aliases") or {}).items():
        values[alias] = values[target]
    constants = document.get("constants") or {}
    missing = {path: set(positions) for path, positions in (document.get("missing") or {}).items()}
    rows = []
    for position in (range(count) if indices is None else indices):
        row: dict[str, Any] = {}
        for path in document.get("order") or []:
            if position in missing.get(path, ()):
                continue
            value = constants[path] if path in constants else values[path][position]
            head, _, sub = path.partition(".")
            if sub and head in NESTED:
                row.setdefault(head, {})[sub] = value
            else:
                row[path] = value
        rows.append(row)
    return rows


def symbol_index(document: Mapping[str, Any]) -> dict[str, int]:
    for path in ("symbol", "ts_code"):
        if path in (document.get("constants") or {}):      # a one-row minute
            return {str(document["constants"][path]).upper(): 0}
        entry = (document.get("columns") or {}).get(path)
        target = (document.get("aliases") or {}).get(path)
        if entry is None and target is not None:
            entry = document["columns"].get(target)
        if entry is not None:
            return {str(symbol).upper(): position for position, symbol in enumerate(_decode(entry)) if symbol}
    return {}


def rows_for(document: Mapping[str, Any], symbols: Sequence[str]) -> dict[str, dict[str, Any]]:
    index = symbol_index(document)
    wanted = [(symbol, index[symbol]) for symbol in symbols if symbol in index]
    return {symbol: row for (symbol, _), row in zip(wanted, rows_of(document, [position for _, position in wanted]))}


# -- storage ------------------------------------------------------------------

def persist_document(database: Any, observed_at: datetime, rows: Sequence[Mapping[str, Any]],
                     metadata: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Write one capture as one row; returns its size so the capture can report it.

    A capture's key is (provider_key, capability, effective_at) with a NULL
    symbol. NULLs never conflict in the table's unique key, so ON CONFLICT
    alone cannot stop a second copy of the same capture - a retried write whose
    first attempt had committed, as the owner side pointed out on 2026-10-09.
    A transaction-scoped advisory lock on the key serialises writers, and a
    capture that already has a document is not written again (first writer
    wins). Once the migration chains are merged, a partial unique index on the
    key can replace the lock.
    """
    document = build_document(rows, observed_at, metadata)
    text = json.dumps(document, ensure_ascii=False, sort_keys=True, default=str)
    digest = hashlib.sha256(text.encode()).hexdigest()
    payload = {"schema": SCHEMA, "rows": document["rows"], "sha256": digest, "metadata": document["metadata"]}
    instant = observed_at if observed_at.tzinfo else observed_at.replace(tzinfo=timezone.utc)
    key = f"{PROVIDER_KEY}:{CAPABILITY}:{instant.astimezone(timezone.utc).isoformat()}"
    with database.transaction() as connection:
        connection.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (key,))
        existing = connection.execute(
            """SELECT payload_sha256 FROM quant.raw_market_observations
                WHERE provider_key=%s AND capability=%s AND symbol IS NULL AND effective_at=%s LIMIT 1""",
            (PROVIDER_KEY, CAPABILITY, observed_at)).fetchone()
        if existing is None:
            connection.execute(
                """INSERT INTO quant.raw_market_observations(provider_key,capability,market,symbol,effective_at,available_at,
                                                              payload_sha256,normalized,payload)
                   VALUES(%s,%s,'cn',NULL,%s,%s,%s,%s::jsonb,%s::jsonb)
                   ON CONFLICT DO NOTHING""",
                (PROVIDER_KEY, CAPABILITY, observed_at, datetime.now(timezone.utc), digest, text,
                 json.dumps(payload, ensure_ascii=False, default=str)))
    return {"rows": document["rows"], "bytes": len(text.encode()), "sha256": digest, "written": existing is None}


_DOCUMENT_SQL = """SELECT effective_at,normalized FROM quant.raw_market_observations
                    WHERE capability=%s AND symbol IS NULL AND provider_key=%s{where}
                    ORDER BY effective_at {order} LIMIT 1"""


def _day_bounds(day: date) -> tuple[datetime, datetime]:
    start = datetime.combine(day, time(0, 0), CN_TZ)
    return start, start + timedelta(days=1)


def _document(row: Any) -> tuple[datetime, dict[str, Any]] | None:
    """A stored minute document, or None for anything else (no row, another payload)."""
    if not row:
        return None
    record = dict(row)
    document = record.get("normalized")
    if isinstance(document, str):
        document = json.loads(document)
    if not isinstance(document, dict) or document.get("schema") != SCHEMA or record.get("effective_at") is None:
        return None
    return record["effective_at"], document


def latest(connection: Any, day: date | None = None) -> tuple[datetime, dict[str, Any]] | None:
    """The newest minute document, of ``day`` if given."""
    if day is None:
        return _document(connection.execute(_DOCUMENT_SQL.format(where="", order="DESC"),
                                            (CAPABILITY, PROVIDER_KEY)).fetchone())
    start, end = _day_bounds(day)
    return _document(connection.execute(
        _DOCUMENT_SQL.format(where=" AND effective_at>=%s AND effective_at<%s", order="DESC"),
        (CAPABILITY, PROVIDER_KEY, start, end)).fetchone())


def first_between(connection: Any, start: datetime, end: datetime) -> tuple[datetime, dict[str, Any]] | None:
    return _document(connection.execute(
        _DOCUMENT_SQL.format(where=" AND effective_at>=%s AND effective_at<%s", order="ASC"),
        (CAPABILITY, PROVIDER_KEY, start, end)).fetchone())


def day_documents(connection: Any, day: date) -> list[tuple[datetime, dict[str, Any]]]:
    """Every minute of a session, oldest first: for a replay or a research panel."""
    start, end = _day_bounds(day)
    rows = connection.execute(
        """SELECT effective_at,normalized FROM quant.raw_market_observations
            WHERE capability=%s AND symbol IS NULL AND provider_key=%s AND effective_at>=%s AND effective_at<%s
            ORDER BY effective_at""", (CAPABILITY, PROVIDER_KEY, start, end)).fetchall()
    return [document for document in (_document(row) for row in rows) if document is not None]


async def latest_async(connection: Any) -> tuple[datetime, dict[str, Any]] | None:
    result = await connection.execute(_DOCUMENT_SQL.format(where="", order="DESC"), (CAPABILITY, PROVIDER_KEY))
    return _document(await result.fetchone())


# -- the newest minute in this process ------------------------------------------

_LATEST: dict[str, Any] = {"observed_at": None, "rows": None}


def remember(observed_at: datetime, rows: Sequence[Mapping[str, Any]]) -> None:
    _LATEST.update(observed_at=observed_at, rows=list(rows))


def recent(now: datetime | None = None) -> tuple[datetime, list[dict[str, Any]]] | None:
    observed_at, rows = _LATEST["observed_at"], _LATEST["rows"]
    if observed_at is None or rows is None:
        return None
    if (now or datetime.now(timezone.utc)) - observed_at > MEMORY_MAX_AGE:
        return None
    return observed_at, rows


def latest_level1_items(observed_at: datetime, rows: Sequence[Mapping[str, Any]], limit: int) -> list[dict[str, Any]]:
    """Items in the shape the per-symbol latest-snapshot read always returned."""
    ordered = sorted(rows, key=lambda row: str(row.get("ts_code") or row.get("symbol") or ""))
    return [{
        "symbol": str(row.get("ts_code") or row.get("symbol") or "").upper(), "effective_at": observed_at,
        "available_at": observed_at, "payload_sha256": None,
        "normalized": {**row, "provider_key": PROVIDER_KEY, "capability": LEGACY_CAPABILITY, "record_index": position},
    } for position, row in enumerate(ordered[:limit])]


__all__ = [
    "CAPABILITY", "LEGACY_CAPABILITY", "SCHEMA", "STORAGE_MODES", "build_document", "day_documents", "first_between",
    "latest", "latest_async", "latest_level1_items", "persist_document", "recent", "remember", "rows_for", "rows_of",
    "storage_mode", "symbol_index",
]
