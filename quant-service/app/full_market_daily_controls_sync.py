"""Bounded same-day control-plane synchronization for full-market daily bars.

This deliberately fetches one already-persisted trading date only.  It is not
part of the historical backfill path: its job is to keep a fresh daily bar's
adjustment factor, trading limits, fundamentals and suspension flag coherent
before strategy/review stages consume that date.
"""

from __future__ import annotations

import asyncio
import re
from datetime import date, datetime, time, timezone
from typing import Any, Awaitable, Callable

from psycopg.types.json import Json

from .adjustment_factor_semantics import persisted_factor_semantics_sql
from .datasources.sources.eastmoney_datacenter import row_symbol
from .owner_factor_repository import FACTOR_PROVIDER_ORDER
from .replay_readiness_coverage import COVERAGE_DEFINITION

CONTROL_APIS = ("adj_factor", "daily_basic", "stk_limit", "suspend_d")
# The factor, fundamentals and limit controls come only from what the owner
# has already persisted for the date (its factor task and the Longhu close).
# Tushare was their remote fallback until 2026-10-08 (decision 0005); a date
# without a complete owner projection now blocks instead.  Suspensions are
# fetched from the Eastmoney datacenter when none are persisted.
SUSPENSION_PROVIDER = "eastmoney_datacenter"
SUSPENSION_CAPABILITY = "suspension_all_a"
# Persisting a same-day full-market projection also refreshes the bounded
# replay-coverage row.  On the owner database this can exceed three minutes
# while the canonical tables are under read load; keep the operation bounded
# but do not cancel a valid atomic write before it can commit.
CONTROL_PERSIST_TIMEOUT_SECONDS = 600
# Listed A-share equity codes only.  A bare six-digit pattern also counted
# index, fund and B-share rows toward the 95% coverage gate.
_A_SHARE = re.compile(
    r"^(?:(?:60[0135]|68[89])\d{3}\.SH|(?:000|001|002|003|300|301|302)\d{3}\.SZ|[489]\d{5}\.BJ)$"
)


def _refresh_same_day_coverage(connection: Any, trade_date: date) -> dict[str, int]:
    """Refresh only the repaired date instead of rescanning historical rows."""
    row = connection.execute(
        """WITH bars AS MATERIALIZED (
                 SELECT symbol
                   FROM quant.canonical_bars_daily
                  WHERE trading_date=%s AND symbol<>'000300.SH'
                    AND available_at < ((trading_date+1)::timestamp AT TIME ZONE 'Asia/Shanghai')
                    AND quality_status='fresh' AND adj_factor>0
             ), universe AS (
                 SELECT count(DISTINCT symbol)::int AS expected_symbols
                   FROM quant.universe_membership_history
                  WHERE universe_key='all_a'
                    AND effective_from<=%s
                    AND (effective_to IS NULL OR effective_to>=%s)
             )
             SELECT coalesce(universe.expected_symbols,0)::int AS expected_symbols,
                    count(DISTINCT bars.symbol)::int AS bar_symbols,
                    count(DISTINCT fundamentals.symbol)::int AS fundamental_symbols,
                    count(DISTINCT limits.symbol)::int AS limit_symbols
               FROM bars CROSS JOIN universe
               LEFT JOIN quant.daily_fundamentals fundamentals
                 ON fundamentals.symbol=bars.symbol AND fundamentals.trading_date=%s
                AND fundamentals.available_at < ((fundamentals.trading_date+1)::timestamp AT TIME ZONE 'Asia/Shanghai')
               LEFT JOIN quant.daily_trade_limits limits
                 ON limits.symbol=bars.symbol AND limits.trading_date=%s
                AND limits.available_at < ((limits.trading_date+1)::timestamp AT TIME ZONE 'Asia/Shanghai')
              GROUP BY universe.expected_symbols""",
        (trade_date, trade_date, trade_date, trade_date, trade_date),
    ).fetchone() or {}
    expected = int(row.get("expected_symbols") or 0)
    bars = int(row.get("bar_symbols") or 0)
    fundamentals = int(row.get("fundamental_symbols") or 0)
    limits = int(row.get("limit_symbols") or 0)
    minimum = max(int(expected * 0.8 + 0.999999), 1000)
    complete = expected >= 1000 and bars >= minimum and fundamentals >= minimum and limits >= minimum
    connection.execute(
        """INSERT INTO quant.replay_readiness_daily_coverage(
               trading_date,expected_symbols,bar_symbols,fundamental_symbols,limit_symbols,
               is_full_cross_section,coverage_definition,refreshed_at)
             VALUES(%s,%s,%s,%s,%s,%s,%s,now())
             ON CONFLICT(trading_date) DO UPDATE SET
               expected_symbols=EXCLUDED.expected_symbols,bar_symbols=EXCLUDED.bar_symbols,
               fundamental_symbols=EXCLUDED.fundamental_symbols,limit_symbols=EXCLUDED.limit_symbols,
               is_full_cross_section=EXCLUDED.is_full_cross_section,
               coverage_definition=EXCLUDED.coverage_definition,refreshed_at=EXCLUDED.refreshed_at""",
        (trade_date, expected, bars, fundamentals, limits, complete, COVERAGE_DEFINITION),
    )
    return {
        "expected_symbols": expected, "bar_symbols": bars,
        "fundamental_symbols": fundamentals, "limit_symbols": limits,
    }


def stamp_date(value: Any) -> date | None:
    """``YYYYMMDD`` (the owner projection's form) or ``YYYY-MM-DD[ hh:mm:ss]``."""
    text = str(value or "").strip()
    try:
        if len(text) == 8 and text.isdigit():
            return date(int(text[:4]), int(text[4:6]), int(text[6:8]))
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _clock(value: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value or "").strip()) if value else None
    except ValueError:
        return None


def normalize_suspensions(rows: list[dict[str, Any]], trade_date: date) -> list[dict[str, Any]]:
    """The A-share equities Eastmoney lists as suspended on ``trade_date``.

    Its date filter also returns a suspension that ended before that session
    opened - 603183.SH ended 2026-10-08 15:00, resumed and traded on 10-09, and
    was still listed for 10-09 - so a row whose end is at or before the
    session's 09:30 is dropped.  A suspension that ends during the session
    still marks the day, as Tushare's ``suspend_d`` did.
    """
    opening = datetime.combine(trade_date, time(9, 30))
    by_symbol: dict[str, dict[str, Any]] = {}
    for row in rows:
        symbol = row_symbol(row)
        start = _clock(row.get("SUSPEND_START_TIME")) or _clock(row.get("SUSPEND_START_DATE"))
        end = _clock(row.get("SUSPEND_END_TIME"))
        if not symbol or not _A_SHARE.fullmatch(symbol) or start is None or start.date() > trade_date:
            continue
        if end is not None and end <= opening:
            continue
        reason = " / ".join(str(part).strip() for part in (row.get("SUSPEND_REASON"), row.get("SUSPEND_EXPIRE")) if part)
        by_symbol[symbol] = {"ts_code": symbol, "trade_date": trade_date.strftime("%Y%m%d"),
                             "suspend_reason": reason or None, "raw": dict(row)}
    return list(by_symbol.values())


def persist_suspensions(connection: Any, trade_date: date, rows: list[dict[str, Any]], available_at: datetime) -> int:
    """One row per security suspended on ``trade_date``, the daily cross-section's meaning."""
    for row in rows:
        connection.execute(
            """INSERT INTO quant.security_suspensions(symbol,suspend_date,resume_date,suspend_reason,provider,available_at,raw)
               SELECT %s,%s,NULL,%s,%s,%s,%s WHERE EXISTS(SELECT 1 FROM quant.instruments WHERE symbol=%s)
               ON CONFLICT(symbol,suspend_date,provider) DO UPDATE SET
                 suspend_reason=EXCLUDED.suspend_reason,available_at=EXCLUDED.available_at,raw=EXCLUDED.raw""",
            (row["ts_code"], trade_date, row["suspend_reason"], SUSPENSION_PROVIDER, available_at,
             Json(row["raw"]), row["ts_code"]),
        )
    return len(rows)


def _record_failure(record_provider_failure: Callable[..., Any], db: Any, error: str) -> None:
    with db.transaction() as connection:
        record_provider_failure(connection, SUSPENSION_PROVIDER, SUSPENSION_CAPABILITY, error, None)


def valid_rows(api_name: str, rows: list[dict[str, Any]], trade_date: date,
               parse_date: Callable[[Any], date | None] = stamp_date) -> list[dict[str, Any]]:
    """Keep only the requested A-share cross-section and remove duplicate codes."""
    by_symbol: dict[str, dict[str, Any]] = {}
    for row in rows:
        symbol = str(row.get("ts_code") or "").upper()
        row_date = parse_date(row.get("trade_date") or row.get("suspend_date"))
        if _A_SHARE.fullmatch(symbol) and row_date == trade_date:
            by_symbol[symbol] = row
    return list(by_symbol.values())


async def sync(
    trade_date: date,
    *,
    expected_daily_rows: Callable[[date], int],
    fetch_suspensions: Callable[[date], Awaitable[list[dict[str, Any]]]],
    run_database_blocking: Callable[..., Awaitable[Any]],
    db: Any,
    safe_error_detail: Callable[[str, int], str],
    executor_saturated_error: type[Exception],
    record_provider_success: Callable[..., Any],
    record_provider_failure: Callable[..., Any],
    read_persisted_factor_controls: Callable[[date, int], Awaitable[Any]] | None = None,
    read_persisted_control_rows: Callable[[str, date, int], Awaitable[Any]] | None = None,
) -> dict[str, Any]:
    """Promote exactly one date of controls after full-market daily.

    All three non-empty control cross-sections must cover at least 95% of the
    already persisted daily universe.  Suspension is legitimately empty and
    is used to establish the safe ``false`` baseline only after the other
    controls have passed their coverage gate.
    """
    expected = await run_database_blocking(expected_daily_rows, trade_date)
    if expected <= 0:
        return {"status": "blocked", "trade_date": str(trade_date), "reason": "full-market daily bars are not ready"}

    started = asyncio.get_running_loop().time()
    results: dict[str, Any] = {}
    rows_by_api: dict[str, list[dict[str, Any]]] = {}
    persisted_control_apis: set[str] = set()
    try:
        if read_persisted_factor_controls is not None:
            persisted = await read_persisted_factor_controls(trade_date, expected)
            factor_rows = valid_rows("adj_factor", list(persisted.get("rows") or []), trade_date)
            if len(factor_rows) < max(1, int(expected * 0.95)):
                raise ValueError(
                    f"owner persisted adjustment factors returned {len(factor_rows)} valid rows; "
                    f"expected at least {max(1, int(expected * 0.95))}"
                )
            results["adj_factor"] = persisted
            rows_by_api["adj_factor"] = factor_rows
            persisted_control_apis.add("adj_factor")
        for api_name in CONTROL_APIS:
            if api_name in persisted_control_apis:
                continue
            if read_persisted_control_rows is not None:
                persisted = await read_persisted_control_rows(api_name, trade_date, expected)
                if persisted:
                    persisted_rows = valid_rows(
                        api_name, list(persisted.get("rows") or []), trade_date,
                    )
                    minimum = 1 if api_name == "suspend_d" else max(1, int(expected * 0.95))
                    if len(persisted_rows) >= minimum:
                        results[api_name] = persisted
                        rows_by_api[api_name] = persisted_rows
                        persisted_control_apis.add(api_name)
                        continue
            if api_name != "suspend_d":
                raise ValueError(
                    f"{api_name}: no complete owner projection for {trade_date}; "
                    "its Tushare fallback was retired on 2026-10-08 (decision 0005)"
                )
            try:
                fetched = await fetch_suspensions(trade_date)
            except executor_saturated_error:
                raise
            except Exception as error:
                await run_database_blocking(_record_failure, record_provider_failure, db, safe_error_detail(str(error), 300))
                raise
            results[api_name] = {"provider": SUSPENSION_PROVIDER, "fetched": len(fetched)}
            rows_by_api[api_name] = normalize_suspensions(fetched, trade_date)
    except executor_saturated_error as error:
        return {"status": "blocked", "trade_date": str(trade_date), "reason": safe_error_detail(str(error), 500)}
    except Exception as error:  # provider result is intentionally not promoted partially
        return {"status": "blocked", "trade_date": str(trade_date), "reason": safe_error_detail(str(error), 500)}

    observed_at = datetime.now(timezone.utc)
    latency_ms = round((asyncio.get_running_loop().time() - started) * 1000)

    def provider_of(api_name: str) -> str:
        if api_name == "adj_factor" and api_name in persisted_control_apis:
            return "owner_persisted_adjustment_factor"
        return str(results[api_name].get("provider") or "owner_persisted")

    def persist() -> dict[str, int]:
        normalized: dict[str, int] = {}
        with db.transaction() as connection:
            # A valid, complete suspend_d response can be empty.  Reset only
            # this completed date, then its actual rows are re-applied below.
            connection.execute(
                "UPDATE quant.canonical_bars_daily SET is_suspended=false,canonicalized_at=now() WHERE trading_date=%s",
                (trade_date,),
            )
            connection.execute(
                "UPDATE quant.market_bars_daily SET is_suspended=false WHERE trading_date=%s",
                (trade_date,),
            )
            for api_name in CONTROL_APIS:
                if api_name in persisted_control_apis:
                    normalized[api_name] = len(rows_by_api[api_name])
                    continue
                # Only suspensions are ever fetched; every other control was persisted.
                normalized[api_name] = persist_suspensions(connection, trade_date, rows_by_api[api_name], observed_at)
                record_provider_success(connection, SUSPENSION_PROVIDER, SUSPENSION_CAPABILITY,
                                        len(rows_by_api[api_name]), latency_ms)
            # Normalization promotes controls directly to canonical bars.  The
            # source bar table is also a strategy/recovery input, so mirror
            # the verified same-provider controls there rather than leaving
            # its current date with NULLs.

            connection.execute(
                f"""WITH selected_factor AS (
                         SELECT DISTINCT ON (factor.trading_date,factor.symbol)
                                factor.trading_date,factor.symbol,factor.adj_factor
                           FROM quant.daily_adjustment_factors factor
                          WHERE factor.trading_date=%s
                            AND {persisted_factor_semantics_sql('factor')}
                            AND factor.adj_factor>0
                            AND factor.available_at<((factor.trading_date+1)::timestamp AT TIME ZONE 'Asia/Shanghai')
                          ORDER BY factor.trading_date,factor.symbol,
                                   array_position(%s::text[],factor.provider) NULLS LAST,
                                   factor.available_at DESC,factor.provider
                     )
                     UPDATE quant.market_bars_daily bar
                        SET adj_factor=selected_factor.adj_factor
                       FROM selected_factor
                      WHERE bar.trading_date=selected_factor.trading_date
                        AND bar.symbol=selected_factor.symbol
                        AND bar.trading_date=%s""",
                (trade_date, list(FACTOR_PROVIDER_ORDER), trade_date),
            )
            connection.execute(
                """UPDATE quant.market_bars_daily bar SET limit_up=limits.limit_up,limit_down=limits.limit_down
                     FROM quant.daily_trade_limits limits
                    WHERE bar.trading_date=%s AND limits.trading_date=bar.trading_date
                      AND limits.symbol=bar.symbol AND limits.provider=%s""",
                (trade_date, provider_of("stk_limit")),
            )
            # Both bar tables were reset above; re-mark them from the selected
            # provider's rows, fetched now or persisted by an earlier run.
            for bars, stamp in (("canonical_bars_daily", ",canonicalized_at=now()"), ("market_bars_daily", "")):
                connection.execute(
                    f"""UPDATE quant.{bars} bar SET is_suspended=true{stamp}
                         FROM quant.security_suspensions suspension
                        WHERE bar.trading_date=%s AND suspension.suspend_date=%s
                          AND suspension.symbol=bar.symbol AND suspension.provider=%s""",
                    (trade_date, trade_date, provider_of("suspend_d")),
                )
            _refresh_same_day_coverage(connection, trade_date)
        return normalized

    # Four complete all-A payloads are promoted in one transaction.  The
    # general ten-second database budget is intentionally too small here and
    # can make a committed write look like a failed caller.  Keep a bounded,
    # explicit budget rather than relying on a worker that outlives its result.
    try:
        normalized = await run_database_blocking(persist, timeout_seconds=CONTROL_PERSIST_TIMEOUT_SECONDS)
    except Exception as error:  # provider rows are never reported as promoted when the atomic write did not return
        return {
            "status": "blocked", "trade_date": str(trade_date),
            "reason": safe_error_detail(f"control projection persistence failed: {error}", 500),
        }
    return {
        "status": "completed", "trade_date": str(trade_date), "expected_daily_rows": expected,
        "rows": {api_name: len(rows) for api_name, rows in rows_by_api.items()}, "normalized_rows": normalized,
        "providers": {api_name: provider_of(api_name) for api_name in results},
        # Tushare's dated ST list has no replacement; readers fall back to the
        # instrument's current flag, which the daily bars keep from the names.
        "st_evidence": {"status": "retired_source", "rows": 0, "provider": None},
    }


__all__ = [
    "CONTROL_APIS", "CONTROL_PERSIST_TIMEOUT_SECONDS", "SUSPENSION_PROVIDER", "normalize_suspensions",
    "persist_suspensions", "stamp_date", "sync", "valid_rows",
]
