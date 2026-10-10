"""Local persistence for public-market evidence.

This repository owns only database writes and reads for public quote, daily
evidence and market events.  It deliberately has no HTTP client, provider
selection or scheduling logic, so slow public providers remain isolated behind
the bounded executor selected by their callers.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Callable
from zoneinfo import ZoneInfo

from psycopg.types.json import Json

from .analysis import as_utc
from .daily_bar_repository import exchange_for
from .instrument_registry import InstrumentRecord, ensure_instruments
from .market_flow_features import market_event_identity_key


def persist_tdx_membership_delta(database: Any, taxonomy_key: str, sector_key: str,
                                 members: dict[str, dict[str, Any]], observed_at: datetime) -> dict[str, int]:
    from .sector_membership_repository import persist_observed_snapshot_delta

    with database.transaction() as connection:
        return persist_observed_snapshot_delta(connection, taxonomy_key, sector_key, members, "tdx_mac", observed_at,
                                               instrument_source="tdx_mac")


def persist_free_quote(database: Any, provider: str, symbol: str, quote: dict[str, Any] | None) -> int:
    if not quote:
        return 0
    payload = json.dumps(quote, ensure_ascii=False, sort_keys=True, default=str)
    with database.transaction() as connection:
        connection.execute(
            """INSERT INTO quant.raw_market_observations(provider_key,capability,market,symbol,effective_at,available_at,payload_sha256,normalized,payload)
               VALUES(%s,'realtime_quote','cn',%s,now(),now(),%s,%s,%s)
               ON CONFLICT(provider_key,capability,market,symbol,effective_at,payload_sha256) DO UPDATE SET available_at=now()""",
            (provider, symbol, hashlib.sha256(payload.encode()).hexdigest(), Json(quote), Json(quote)),
        )
    return 1


def persist_free_quotes(database: Any, provider: str, quotes: list[dict[str, Any]]) -> int:
    """Write a market quote batch in one transaction; malformed rows are skipped."""
    parameters = []
    observed_at = datetime.now(timezone.utc)
    for quote in quotes:
        symbol = str(quote.get("ts_code") or "").upper()
        if not re.fullmatch(r"\d{6}\.(SH|SZ|BJ)", symbol):
            continue
        payload = json.dumps(quote, ensure_ascii=False, sort_keys=True, default=str)
        parameters.append((
            provider, symbol, observed_at, observed_at,
            hashlib.sha256(payload.encode()).hexdigest(), Json(quote), Json(quote),
        ))
    if not parameters:
        return 0
    with database.transaction() as connection:
        with connection.cursor() as cursor:
            cursor.executemany(
                """INSERT INTO quant.raw_market_observations(provider_key,capability,market,symbol,effective_at,available_at,payload_sha256,normalized,payload)
                   VALUES(%s,'realtime_quote','cn',%s,%s,%s,%s,%s,%s)
                   ON CONFLICT(provider_key,capability,market,symbol,effective_at,payload_sha256) DO UPDATE SET available_at=EXCLUDED.available_at""",
                parameters,
            )
    return len(parameters)


def _observation_symbol(value: Any) -> str | None:
    symbol = str(value or "").upper() or None
    return symbol if symbol and re.fullmatch(r"\d{6}\.(SH|SZ|BJ)", symbol) else None


def persist_public_observations(database: Any, provider: str, capability: str,
                                rows: list[dict[str, Any]], symbol: str | None = None) -> int:
    """Persist public aggregate rows as raw evidence without canonical promotion.

    One ``executemany`` per batch: the owner database is 52ms away over the
    tunnel, and a 5,500-row cross-section written row by row spent minutes
    waiting on the network.  The statement per row is unchanged.
    """
    observed_at = datetime.now(timezone.utc)
    parameters = []
    for index, row in enumerate(rows):
        row_symbol = _observation_symbol(row.get("ts_code") or symbol)
        payload = {**row, "provider_key": provider, "capability": capability, "record_index": index}
        serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
        parameters.append((provider, capability, row_symbol, observed_at, observed_at,
                           hashlib.sha256(serialized.encode()).hexdigest(), Json(payload), Json(payload)))
    if not parameters:
        return 0
    with database.transaction() as connection:
        with connection.cursor() as cursor:
            cursor.executemany(
                """INSERT INTO quant.raw_market_observations(provider_key,capability,market,symbol,effective_at,available_at,payload_sha256,normalized,payload)
                   VALUES(%s,%s,'cn',%s,%s,%s,%s,%s,%s)
                   ON CONFLICT(provider_key,capability,market,symbol,effective_at,payload_sha256) DO UPDATE SET available_at=EXCLUDED.available_at""",
                parameters,
            )
    return len(parameters)


def persist_timed_observations(database: Any, provider: str, capability: str,
                               rows: list[dict[str, Any]]) -> int:
    """Persist observations that carry their own ``effective_at``/``available_at``.

    For immutable published facts -- a news flash, a settled ranking, a NAV --
    the first capture is the point-in-time truth, so a repeat capture of the
    same payload is ignored rather than moving ``available_at`` later.  The
    two clocks are removed from the stored payload so an identical fact seen
    twice hashes identically.
    """
    parameters = []
    fallback = datetime.now(timezone.utc)
    for row in rows:
        payload = {key: value for key, value in row.items()
                   if key not in {"effective_at", "available_at", "availability_basis"}}
        availability_basis = row.get("availability_basis")
        effective = as_utc(datetime.fromisoformat(str(row["effective_at"]))) if row.get("effective_at") else fallback
        available = as_utc(datetime.fromisoformat(str(row["available_at"]))) if row.get("available_at") else fallback
        payload.update({"provider_key": provider, "capability": capability})
        serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
        parameters.append((provider, capability, _observation_symbol(row.get("ts_code")), effective, available,
                           availability_basis, hashlib.sha256(serialized.encode()).hexdigest(), Json(payload), Json(payload)))
    if not parameters:
        return 0
    with database.transaction() as connection:
        with connection.cursor() as cursor:
            cursor.executemany(
                """INSERT INTO quant.raw_market_observations(provider_key,capability,market,symbol,effective_at,available_at,availability_basis,payload_sha256,normalized,payload)
                   VALUES(%s,%s,'cn',%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT(provider_key,capability,market,symbol,effective_at,payload_sha256) DO NOTHING""",
                parameters,
            )
    return len(parameters)


def latest_observation_payloads(database: Any, provider: str, capability: str) -> dict[str, dict[str, Any]]:
    """Return the latest stored payload for each symbol of one public capability."""
    with database.transaction() as connection:
        rows = connection.execute(
            """SELECT DISTINCT ON (symbol) symbol,normalized
               FROM quant.raw_market_observations
               WHERE provider_key=%s AND capability=%s AND symbol IS NOT NULL
               ORDER BY symbol,effective_at DESC,created_at DESC""",
            (provider, capability),
        ).fetchall()
    return {str(row["symbol"]): dict(row["normalized"]) for row in rows}


def observation_payloads(database: Any, provider: str, capability: str) -> list[dict[str, Any]]:
    """Read all stored normalized payloads for one public capability."""
    with database.transaction() as connection:
        rows = connection.execute(
            """SELECT normalized FROM quant.raw_market_observations
               WHERE provider_key=%s AND capability=%s
               ORDER BY effective_at,created_at""",
            (provider, capability),
        ).fetchall()
    return [dict(row["normalized"]) for row in rows]


def persist_free_daily(
    database: Any,
    provider: str,
    rows: list[dict[str, Any]],
    *,
    daily_bar_type: Any,
    parse_trade_date: Callable[[Any], Any],
    decimal_or_none: Callable[[Any], Any],
    upsert_bar: Callable[[Any, Any], None],
    persist_raw_observations: Callable[[Any, str, str, list[dict[str, Any]]], int],
    observed_at: datetime | None = None,
) -> int:
    """Promote only validated unadjusted public daily rows in one transaction.

    Tencent's adapter is explicitly front-adjusted.  Its short-window bars
    remain attributable raw evidence, never a fallback for the canonical
    unadjusted series when a licensed provider has a gap.
    """
    if provider == "tencent_free":
        return persist_raw_observations(database, provider, "daily_bar", rows)

    available_at = observed_at or datetime.now(timezone.utc)
    valid_bars: list[Any] = []
    for row in rows:
        try:
            trading_date = parse_trade_date(row.get("trade_date"))
            symbol = str(row.get("ts_code") or "").upper()
            if not trading_date or not re.fullmatch(r"\d{6}\.(SH|SZ|BJ)", symbol):
                raise ValueError("free daily row is missing a valid symbol or trading date")
            valid_bars.append(daily_bar_type(
                symbol=symbol, trading_date=trading_date, close=decimal_or_none(row.get("close")),
                open=decimal_or_none(row.get("open")), high=decimal_or_none(row.get("high")),
                low=decimal_or_none(row.get("low")), volume=decimal_or_none(row.get("vol")),
                amount=decimal_or_none(row.get("amount")), source=provider, available_at=available_at,
            ))
        except Exception:
            # The caller records source health.  A malformed public row must
            # never displace licensed canonical data.
            continue
    if not valid_bars:
        return 0
    with database.transaction() as connection:
        for bar in valid_bars:
            upsert_bar(connection, bar)
    return len(valid_bars)


def persist_market_events(database: Any, provider: str, rows: list[dict[str, Any]]) -> int:
    """Store public event evidence without making it a hard trading signal.

    Rows are validated in Python and written with three batched statements
    (instruments, identity-keyed events, content-keyed events) instead of two
    statements per row -- the per-row form cost ~100ms a row over the owner
    tunnel.  Each statement still executes once per row, so a key repeated
    within one batch resolves through ``ON CONFLICT`` in arrival order.
    """
    instruments: dict[str, tuple[str, str, str]] = {}
    keyed, content_keyed = [], []
    for row in rows:
        symbol = str(row.get("ts_code") or "").upper()
        title = str(row.get("title") or row.get("short_title") or "").strip()
        url = str(row.get("url") or "").strip() or None
        if not re.fullmatch(r"\d{6}\.(SH|SZ|BJ)", symbol) or not title:
            continue
        published_at = as_utc(datetime.fromisoformat(str(row["published_at"]))) if row.get("published_at") else datetime.now(timezone.utc)
        payload = json.dumps(row, ensure_ascii=False, sort_keys=True, default=str)
        content_sha256 = hashlib.sha256(payload.encode()).hexdigest()
        event_type = str(row.get("event_type") or "announcement")
        occurred_date = published_at.astimezone(ZoneInfo("Asia/Shanghai")).date().isoformat()
        identity_key = str(row.get("event_identity_key") or "").strip() or market_event_identity_key(
            provider, event_type, symbol, occurred_date,
        )
        instruments.setdefault(symbol, (symbol, exchange_for(symbol), provider))
        values = (
            uuid.uuid4(), symbol, event_type, published_at, published_at, provider, title,
            json.dumps(row.get("raw") or row, ensure_ascii=False, default=str), url, content_sha256, identity_key,
        )
        (keyed if identity_key is not None else content_keyed).append(values)
    if not keyed and not content_keyed:
        return 0
    with database.transaction() as connection:
        ensure_instruments(
            connection,
            [InstrumentRecord(symbol=symbol, exchange=exchange, source=instrument_source)
             for symbol, exchange, instrument_source in instruments.values()],
            source=provider,
        )
        with connection.cursor() as cursor:
            if keyed:
                cursor.executemany(
                    """INSERT INTO quant.market_events(
                           event_id,symbol,event_type,occurred_at,available_at,source,title,body,url,content_sha256,event_identity_key)
                       VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                       ON CONFLICT(event_identity_key) WHERE event_identity_key IS NOT NULL DO UPDATE SET
                         available_at=LEAST(quant.market_events.available_at,EXCLUDED.available_at),
                         title=EXCLUDED.title,body=EXCLUDED.body,url=EXCLUDED.url,
                         content_sha256=EXCLUDED.content_sha256""",
                    keyed,
                )
            if content_keyed:
                cursor.executemany(
                    """INSERT INTO quant.market_events(
                           event_id,symbol,event_type,occurred_at,available_at,source,title,body,url,content_sha256,event_identity_key)
                       VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                       ON CONFLICT(content_sha256) DO UPDATE SET
                         available_at=LEAST(quant.market_events.available_at,EXCLUDED.available_at),
                         title=EXCLUDED.title,body=EXCLUDED.body,url=EXCLUDED.url""",
                    content_keyed,
                )
    return len(keyed) + len(content_keyed)


def recent_market_events(database: Any, symbol: str, limit: int = 20) -> list[dict[str, Any]]:
    with database.transaction() as connection:
        rows = connection.execute(
            """SELECT event_id,symbol,event_type,occurred_at,available_at,source,title,url,created_at
                 FROM quant.market_events WHERE symbol=%s ORDER BY occurred_at DESC,created_at DESC LIMIT %s""",
            (symbol, max(1, min(limit, 100))),
        ).fetchall()
    return [dict(row) for row in rows]


__all__ = [
    "persist_free_daily", "persist_free_quote", "persist_free_quotes", "persist_market_events",
    "persist_public_observations", "persist_timed_observations", "recent_market_events",
]
