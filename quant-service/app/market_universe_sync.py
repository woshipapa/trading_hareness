"""Dependency-injected active A-share universe synchronization.

The authoritative listing behind ``all_a`` - the one listing that may also
*remove* members - is Fuyao's ``ticker_list`` (``asset_type=a-share``).  It
replaced Tushare's ``stock_basic`` on 2026-10-09 (decision 0005).  The Longhu
close only ever adds members; this listing is what retires a delisted name
and what dates every instrument's ``list_date``.

``list_date`` is not cosmetic: the market volume baseline discriminates index
series from listed names by exactly that column, so with none of them dated
the baseline was null, the index volume ratio was null, and the leader-flow
market gate rejected all 250 pool members for incomplete fields.

Because this listing removes members, a short or lopsided answer must never
be promoted: the whole list is paged to its end, and it must reach the
request's ``minimum_rows`` and span all three exchanges before anything is
written.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import date, datetime, timezone
from typing import Any, Awaitable, Callable

from psycopg.types.json import Json

from .datasources.http import ashare_symbol
from .universe_history import sync_universe_membership_history

PROVIDER_KEY = "fuyao_ths"
PAGE_SIZE = 1000
MAX_PAGES = 20
REQUIRED_EXCHANGES = frozenset({"SH", "SZ", "BJ"})


def _day(value: Any) -> date | None:
    text = str(value or "").strip()[:10]
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def listed_rows(items: list[dict[str, Any]], today: date) -> dict[str, dict[str, Any]]:
    """``stock_basic``-shaped rows for the A-share equities still listed on ``today``."""
    by_symbol: dict[str, dict[str, Any]] = {}
    for item in items:
        symbol = ashare_symbol(item.get("thscode"))
        if symbol is None or str(item.get("asset_type") or "") != "a-share":
            continue
        listed, ended = _day(item.get("list_date")), _day(item.get("end_date"))
        if ended is not None and ended <= today:
            continue
        by_symbol[symbol] = {
            "ts_code": symbol, "name": item.get("name"), "exchange": symbol[-2:], "industry": None,
            "list_date": listed.strftime("%Y%m%d") if listed else None,
            "delist_date": ended.strftime("%Y%m%d") if ended else None,
        }
    return by_symbol


async def fetch_listing(fetch: Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]) -> list[dict[str, Any]]:
    """Every page of the A-share ticker list; a list that does not end is an error, not a result."""
    items: list[dict[str, Any]] = []
    for page in range(MAX_PAGES):
        data = await fetch("ticker_list", {"asset_type": "a-share", "limit": PAGE_SIZE, "offset": page * PAGE_SIZE})
        batch = [dict(item) for item in (data or {}).get("item") or [] if isinstance(item, dict)]
        items.extend(batch)
        if len(batch) < PAGE_SIZE:
            return items
    raise RuntimeError(f"Fuyao ticker_list did not end within {MAX_PAGES} pages of {PAGE_SIZE}")


async def sync(
    request: Any,
    *,
    fetch: Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]],
    cn_date: Callable[[], Any],
    persist_rows: Callable[..., int],
    run_database_blocking: Callable[..., Awaitable[Any]],
    db: Any,
    safe_error_detail: Callable[[str, int], str],
    executor_saturated_error: type[Exception],
    record_provider_success: Callable[..., Any],
    record_provider_failure: Callable[..., Any],
) -> dict[str, Any]:
    """Refresh ``all_a`` from the complete A-share listing, or change nothing."""
    exchange_date = cn_date()
    request_key = hashlib.sha256(json.dumps({"capability": "stock_basic_all_a", "date": str(exchange_date), "provider": request.provider}, sort_keys=True).encode()).hexdigest()

    def prepare_run() -> dict[str, Any] | None:
        with db.transaction() as connection:
            prior = connection.execute("SELECT status,row_count FROM quant.fetch_runs WHERE request_key=%s", (request_key,)).fetchone()
            if prior and prior["status"] == "completed":
                return {"status": "unchanged", "universe_key": request.universe_key, "imported": prior["row_count"], "request_key": request_key}
            connection.execute(
                """INSERT INTO quant.fetch_runs(provider_key,capability,trade_date,request_key,status,attempt_count,started_at,metadata)
                   VALUES(%s,'stock_basic_all_a',%s,%s,'running',1,now(),%s)
                   ON CONFLICT(request_key) DO UPDATE SET status='running',attempt_count=quant.fetch_runs.attempt_count+1,
                     started_at=now(),finished_at=null,error_class=null,error_message=null""",
                (PROVIDER_KEY, exchange_date, request_key, Json({"universe_key": request.universe_key, "minimum_rows": request.minimum_rows})),
            )
        return None

    unchanged = await run_database_blocking(prepare_run)
    if unchanged:
        return unchanged
    provider_started_at = asyncio.get_running_loop().time()
    try:
        valid_by_symbol = listed_rows(await fetch_listing(fetch), exchange_date)
        valid_rows = list(valid_by_symbol.values())
        if len(valid_rows) < request.minimum_rows:
            raise RuntimeError(f"ticker_list returned {len(valid_rows)} listed A-share symbols; expected at least {request.minimum_rows}")
        missing = REQUIRED_EXCHANGES - {symbol[-2:] for symbol in valid_by_symbol}
        if missing:
            raise RuntimeError(f"ticker_list has no {sorted(missing)} symbols; a listing missing an exchange would retire it")
        observed_at = datetime.now(timezone.utc)
        provider_latency_ms = round((asyncio.get_running_loop().time() - provider_started_at) * 1000)

        def persist_result() -> int:
            with db.transaction() as connection:
                normalized = persist_rows(connection, "stock_basic", request_key, valid_rows, PROVIDER_KEY, observed_at)
                for symbol in valid_by_symbol:
                    connection.execute(
                        """INSERT INTO quant.universe_members(universe_key,symbol,enabled,priority,source,metadata,updated_at)
                           VALUES(%s,%s,true,1000,'stock-basic-all-a',%s,now())
                           ON CONFLICT(universe_key,symbol) DO UPDATE SET enabled=true,source=EXCLUDED.source,metadata=EXCLUDED.metadata,updated_at=now()""",
                        (request.universe_key, symbol, Json({"provider": PROVIDER_KEY, "reference_date": str(exchange_date)})),
                    )
                connection.execute(
                    """UPDATE quant.universe_members SET enabled=false,updated_at=now()
                         WHERE universe_key=%s AND source='stock-basic-all-a' AND enabled
                           AND NOT symbol = ANY(%s)""",
                    (request.universe_key, list(valid_by_symbol)),
                )
                sync_universe_membership_history(
                    connection, request.universe_key, exchange_date, valid_by_symbol,
                    source=f"stock-basic-all-a:{PROVIDER_KEY}", priority=1000,
                )
                connection.execute("UPDATE quant.fetch_runs SET status='completed',row_count=%s,finished_at=now() WHERE request_key=%s", (len(valid_rows), request_key))
                record_provider_success(connection, PROVIDER_KEY, "stock_basic_all_a", len(valid_rows), provider_latency_ms)
            return normalized

        normalized = await run_database_blocking(persist_result)
        return {"status": "completed", "universe_key": request.universe_key, "imported": len(valid_rows), "normalized_rows": normalized,
                "provider": PROVIDER_KEY, "request_key": request_key}
    except executor_saturated_error as error:
        return {"status": "blocked", "universe_key": request.universe_key, "reason": safe_error_detail(str(error), 500), "request_key": request_key}
    except Exception as error:  # noqa: BLE001 - provider failures are persisted and returned safely
        failure_latency_ms = round((asyncio.get_running_loop().time() - provider_started_at) * 1000)
        # A timeout's message is empty; on 2026-10-09 that left the receipt and the
        # provider health with no reason at all. The exception's type always says something.
        detail = safe_error_detail(f"{type(error).__name__}: {error}".rstrip(": "), 1000)

        def persist_failure() -> None:
            with db.transaction() as connection:
                connection.execute("UPDATE quant.fetch_runs SET status='failed',finished_at=now(),error_class='provider_error',error_message=%s WHERE request_key=%s", (detail, request_key))
                record_provider_failure(connection, PROVIDER_KEY, "stock_basic_all_a", detail, failure_latency_ms)

        await run_database_blocking(persist_failure)
        return {"status": "blocked", "universe_key": request.universe_key, "reason": detail[:500], "request_key": request_key}


__all__ = ["MAX_PAGES", "PAGE_SIZE", "PROVIDER_KEY", "REQUIRED_EXCHANGES", "fetch_listing", "listed_rows", "sync"]
