"""Bounded minute-session capture for explicit watchlist symbols.

The persistence seam accepts a source-aware fetcher so Longhu can be primary
when its dated minute response is available, while Tencent remains a fallback.
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta, timezone
import re
from typing import Any, Awaitable, Callable
from zoneinfo import ZoneInfo

from .longhu_vendor_source import current_session_minute_rows
from .stable_json import tolerant_json


_SOURCE_NAMES = {
    "tencent_intraday_minutes",
    "longhu_intraday_minutes",
    "tushare_super_get_rt_min_daily",
    "tushare_super_rt_min_daily",
}


def minute_row_datetime(raw_row: dict[str, Any], trading_date: date) -> datetime | None:
    """Resolve a minute row only if its explicit date matches the session.

    HH:MM-only public rows are interpreted in the requested session (the
    existing Tencent contract).  A provider-supplied date/time is authoritative
    and a different date is rejected rather than silently relabelled as today.
    """
    explicit = raw_row.get("trade_time") or raw_row.get("datetime") or raw_row.get("bar_time")
    if not explicit and raw_row.get("trade_date"):
        explicit = f"{raw_row.get('trade_date')} {raw_row.get('time') or ''}".strip()
    if explicit:
        text = str(explicit).strip()
        parsed: datetime | None = None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            for fmt in ("%Y%m%d%H%M%S", "%Y%m%d%H%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
                try:
                    parsed = datetime.strptime(text, fmt)
                    break
                except ValueError:
                    continue
        if parsed is None:
            digits = "".join(character for character in text if character.isdigit())
            if len(digits) >= 14:
                try:
                    parsed = datetime.strptime(digits[:14], "%Y%m%d%H%M%S")
                except ValueError:
                    parsed = None
            elif len(digits) >= 12:
                try:
                    parsed = datetime.strptime(digits[:12], "%Y%m%d%H%M")
                except ValueError:
                    parsed = None
            elif len(digits) == 8:
                try:
                    parsed = datetime.strptime(f"{digits}000000", "%Y%m%d%H%M%S")
                except ValueError:
                    parsed = None
        if parsed is None:
            return None
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone(ZoneInfo("Asia/Shanghai")).replace(tzinfo=None)
        if parsed.date() != trading_date:
            return None
        return parsed
    clock = str(raw_row.get("time") or "").strip()
    if re.fullmatch(r"\d{4}", clock):
        clock = f"{clock[:2]}:{clock[2:]}"
    try:
        parsed = datetime.strptime(f"{trading_date.isoformat()} {clock}", "%Y-%m-%d %H:%M")
    except ValueError:
        return None
    return parsed


def minute_storage_source(source: dict[str, Any] | None) -> str:
    """Map provider metadata to a bounded storage label."""
    source = source or {}
    candidate = str(source.get("storage_source") or source.get("source_name") or "").strip()
    return candidate if candidate in _SOURCE_NAMES else "tencent_intraday_minutes"


async def fetch_longhu_first_minute_rows(
    symbol: str, *, observed_at: datetime,
    longhu_fetch: Callable[[str], Awaitable[list[dict[str, Any]]]],
    fallback_fetch: Callable[[str], Awaitable[list[dict[str, Any]]]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Use dated Longhu minutes first, then a clearly-labelled fallback.

    Missing or stale Longhu dates are treated as provider failure.  We never
    stamp the current date onto those rows; the fallback remains explicit in
    the persisted source status and therefore cannot masquerade as Longhu.
    """
    try:
        rows = current_session_minute_rows(await longhu_fetch(symbol), observed_at=observed_at)
        if not rows:
            raise RuntimeError("Longhu minute returned no dated rows")
    except Exception as longhu_error:  # noqa: BLE001 - fallback is per symbol
        rows = await fallback_fetch(symbol)
        return rows, {
            "provider": "tencent_free", "api_name": "minute/query", "status": "fallback",
            "storage_source": "tencent_intraday_minutes",
            "primary_provider": "longhuvip", "primary_error": str(longhu_error)[:240],
        }
    return rows, {
        "provider": "longhuvip", "api_name": "GetStockTrendIncremental", "status": "completed",
        "storage_source": "longhu_intraday_minutes",
    }


def store_symbol_minutes(connection: Any, symbol: str, rows: list[dict[str, Any]], trading_date: date,
                         source_name: str, parse_minute: Callable[[dict[str, Any]], dict[str, Any]]) -> tuple[int, str | None]:
    """Upsert one symbol's session rows in one batched statement; returns (stored, first error).

    Rows are validated first and written with a single ``executemany``: one
    INSERT per minute over the ~100 ms tunnel took ~9 s a stock.
    """
    first_error = None
    params: list[tuple[Any, ...]] = []
    available_at = datetime.now(timezone.utc)
    for raw_row in rows:
        try:
            resolved_time = minute_row_datetime(raw_row, trading_date)
            if resolved_time is None:
                first_error = first_error or "minute row has missing or stale exchange date"
                continue
            # These provider rows expose a close/cumulative profile rather than
            # guaranteed true minute OHLC.  Keep the existing flat-bar
            # compatibility shape, but preserve the raw source and provenance.
            row = parse_minute({
                **raw_row, "ts_code": symbol,
                "datetime": resolved_time.isoformat(sep=" "),
                "open": raw_row.get("close"), "high": raw_row.get("close"), "low": raw_row.get("close"),
            })
            local_bar_time = row["bar_time"].astimezone(ZoneInfo("Asia/Shanghai"))
            if local_bar_time.date() != trading_date:
                continue
            params.append((symbol, trading_date, local_bar_time.strftime("%H:%M"), row["bar_time"], row["open"], row["high"],
                           row["low"], row["close"], row["volume"], row["amount"], source_name, available_at,
                           tolerant_json(row["raw"])))
        except (ValueError, TypeError) as validation_error:
            first_error = first_error or f"invalid minute row: {str(validation_error)[:200]}"
    if params:
        connection.executemany(
            """INSERT INTO quant.intraday_minute_sessions(
                   symbol,trading_date,minute_bucket,bar_time,open,high,low,close,volume,amount,source_name,available_at,raw
               ) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT(symbol,trading_date,minute_bucket,source_name) DO UPDATE SET
                   bar_time=EXCLUDED.bar_time,open=EXCLUDED.open,high=EXCLUDED.high,low=EXCLUDED.low,
                   close=EXCLUDED.close,volume=EXCLUDED.volume,amount=EXCLUDED.amount,
                   available_at=EXCLUDED.available_at,raw=EXCLUDED.raw""",
            params,
        )
    return len(params), first_error


def store_session_minutes(database: Any, trading_date: date, rows_by_symbol: dict[str, list[dict[str, Any]]], *,
                          source_name: str, parse_minute: Callable[[dict[str, Any]], dict[str, Any]],
                          ensure_instrument: Callable[[Any, str], None]) -> dict[str, Any]:
    """Store whole sessions for explicit watch symbols (the post-close review's copy).

    Additive only: rows are upserted by (symbol, date, minute, source) and no
    retention delete runs here - that stays with the close-window capture.
    """
    if source_name not in _SOURCE_NAMES:
        raise ValueError(f"unknown minute source {source_name}")
    stored_by_symbol: dict[str, int] = {}
    errors: dict[str, str] = {}
    with database.transaction() as connection:
        for symbol, rows in sorted(rows_by_symbol.items()):
            ensure_instrument(connection, symbol)
            stored, error = store_symbol_minutes(connection, symbol, rows, trading_date, source_name, parse_minute)
            stored_by_symbol[symbol] = stored
            if error:
                errors[symbol] = error
    counts = sorted(stored_by_symbol.values())
    return {"status": "completed" if counts and not errors else "partial" if counts else "empty",
            "symbols": len(stored_by_symbol), "stored": sum(counts), "source_name": source_name,
            "per_symbol_min_max": [counts[0], counts[-1]] if counts else None, "errors": dict(list(errors.items())[:5])}


class IntradayMinuteCaptureActions:
    """Persist only current-session minute profiles, never a broad minute archive."""

    def __init__(self, database: Any) -> None:
        self._database = database

    async def capture(
        self,
        symbols: list[str],
        *,
        realtime_session: Callable[[], Awaitable[tuple[bool, str]]],
        fetch_minutes: Callable[[str], Awaitable[list[dict[str, Any]]]],
        fetch_minutes_with_source: Callable[
            [str], Awaitable[tuple[list[dict[str, Any]], dict[str, Any]]]
        ] | None = None,
        run_database: Callable[..., Awaitable[Any]],
        parse_minute: Callable[[dict[str, Any]], dict[str, Any]],
        ensure_instrument: Callable[[Any, str], None],
        retention_days: Callable[[], int],
    ) -> dict[str, Any]:
        """Capture current-session rows through an injected persistence seam."""
        active, reason = await realtime_session()
        if not active:
            return {"status": "blocked", "reason": reason, "stored": 0, "symbols": symbols}
        local_now = datetime.now(timezone.utc).astimezone(ZoneInfo("Asia/Shanghai"))
        trading_date = local_now.date()

        async def fetch_one(symbol: str) -> tuple[str, list[dict[str, Any]], dict[str, Any] | None, str | None]:
            try:
                if fetch_minutes_with_source is not None:
                    rows, source = await fetch_minutes_with_source(symbol)
                else:
                    rows = await fetch_minutes(symbol)
                    source = {"provider": "tencent_free", "api_name": "minute/query", "status": "completed"}
                return symbol, rows, source, None
            # Transport ownership stays inside the injected provider adapter.
            # This action only turns its bounded failure into per-symbol
            # research evidence; it must not import or classify HTTP clients.
            except Exception as error:  # noqa: BLE001 - one symbol must not stop the bounded basket
                return symbol, [], None, str(error)[:300]

        results = await asyncio.gather(*(fetch_one(symbol) for symbol in symbols), return_exceptions=True)
        keep_days = retention_days()
        retention_start = trading_date - timedelta(days=keep_days)

        def persist_sessions() -> tuple[dict[str, int], dict[str, str], dict[str, Any]]:
            stored_by_symbol: dict[str, int] = {}
            errors: dict[str, str] = {}
            source_status: dict[str, Any] = {}
            with self._database.transaction() as connection:
                for result in results:
                    if isinstance(result, Exception):
                        errors["unknown"] = str(result)[:300]
                        continue
                    symbol, rows, source, error = result
                    if error:
                        errors[symbol] = error
                        continue
                    source_status[symbol] = source
                    source_name = minute_storage_source(source)
                    ensure_instrument(connection, symbol)
                    stored, error = store_symbol_minutes(connection, symbol, rows, trading_date, source_name, parse_minute)
                    if error:
                        errors.setdefault(symbol, error)
                    stored_by_symbol[symbol] = stored
                    connection.execute(
                        """DELETE FROM quant.intraday_minute_sessions
                             WHERE symbol=%s AND trading_date<%s
                               AND source_name IN ('tushare_super_get_rt_min_daily','tushare_super_rt_min_daily','tencent_intraday_minutes','longhu_intraday_minutes')""",
                        (symbol, retention_start),
                    )
            return stored_by_symbol, errors, source_status

        stored_by_symbol, errors, source_status = await run_database(persist_sessions, timeout_seconds=60)
        stored_total = sum(stored_by_symbol.values())
        status = "completed" if stored_total and not errors else "partial" if stored_total else "failed"
        return {
            "status": status, "trading_date": str(trading_date), "symbols": symbols, "stored": stored_total,
            "stored_by_symbol": stored_by_symbol, "errors": errors, "source_status": source_status,
            "retention_days": keep_days,
            "notice": "仅保存显式观察池的盘末分钟剖面，用于历史同刻量能基线；不构成全市场分钟归档。",
        }
