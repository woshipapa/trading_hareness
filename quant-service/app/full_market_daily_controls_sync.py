"""Bounded same-day control-plane synchronization for full-market daily bars.

This deliberately fetches one already-persisted trading date only.  It is not
part of the historical backfill path: its job is to keep a fresh daily bar's
adjustment factor, trading limits, fundamentals and suspension flag coherent
before strategy/review stages consume that date.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from datetime import date, datetime, timezone
from typing import Any, Awaitable, Callable

from .adjustment_factor_semantics import persisted_factor_semantics_sql
from .owner_factor_repository import FACTOR_PROVIDER_ORDER
from .replay_readiness_coverage import COVERAGE_DEFINITION

CONTROL_APIS = ("adj_factor", "daily_basic", "stk_limit", "suspend_d")
# These are full-cross-section controls. The audited Super route is the only
# compatible capability chain for them; the REST backup is intentionally not
# allowed to consume the shared queue because it is reference/partial-only
# and cannot satisfy this gate. ``super`` expands to ProMax GET first and the
# Super SDK fallback according to the provider catalog.
CONTROL_PROVIDER_PREFERENCE = "super"
# Persisting a same-day full-market projection also refreshes the bounded
# replay-coverage row.  On the owner database this can exceed three minutes
# while the canonical tables are under read load; keep the operation bounded
# but do not cancel a valid atomic write before it can commit.
CONTROL_PERSIST_TIMEOUT_SECONDS = 600
# Whole-market control calls must page: an unpaged stk_limit is refused or cut
# short by the vendor (2,359 of ~5,700 names on 2026-09-16), and a short table
# could still clear the coverage gate below.  Same bounds as tushare_limits.
CONTROL_PAGE_SIZE = 2000
CONTROL_MAX_ROWS = 12000
CONTROL_MAX_PAGES = 8
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


def valid_rows(api_name: str, rows: list[dict[str, Any]], trade_date: date, parse_date: Callable[[Any], date | None]) -> list[dict[str, Any]]:
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
    call_tushare_api: Callable[..., Awaitable[Any]],
    parse_date: Callable[[Any], date | None],
    persist_tushare_rows: Callable[..., int],
    persist_blocked: Callable[..., Any],
    run_database_blocking: Callable[..., Awaitable[Any]],
    db: Any,
    safe_error_detail: Callable[[str, int], str],
    executor_saturated_error: type[Exception],
    record_provider_success: Callable[..., Any],
    record_provider_failure: Callable[..., Any],
    record_provider_api_capability: Callable[..., Any],
    read_persisted_factor_controls: Callable[[date, int], Awaitable[Any]] | None = None,
    read_persisted_control_rows: Callable[[str, date, int], Awaitable[Any]] | None = None,
) -> dict[str, Any]:
    """Fetch and promote exactly one date of controls after full-market daily.

    All three non-empty control cross-sections must cover at least 95% of the
    already persisted daily universe.  Suspension is legitimately empty and
    is used to establish the safe ``false`` baseline only after the other
    controls have passed their coverage gate.
    """
    expected = await run_database_blocking(expected_daily_rows, trade_date)
    if expected <= 0:
        return {"status": "blocked", "trade_date": str(trade_date), "reason": "full-market daily bars are not ready"}

    stamp = trade_date.strftime("%Y%m%d")
    started = asyncio.get_running_loop().time()
    results: dict[str, Any] = {}
    rows_by_api: dict[str, list[dict[str, Any]]] = {}
    persisted_control_apis: set[str] = set()
    try:
        if read_persisted_factor_controls is not None:
            persisted = await read_persisted_factor_controls(trade_date, expected)
            factor_rows = valid_rows("adj_factor", list(persisted.get("rows") or []), trade_date, parse_date)
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
                        api_name, list(persisted.get("rows") or []), trade_date, parse_date,
                    )
                    minimum = 1 if api_name == "suspend_d" else max(1, int(expected * 0.95))
                    if len(persisted_rows) >= minimum:
                        results[api_name] = persisted
                        rows_by_api[api_name] = persisted_rows
                        persisted_control_apis.add(api_name)
                        continue
            result = await call_tushare_api(
                api_name, {"trade_date": stamp}, None, CONTROL_PROVIDER_PREFERENCE,
                paginate=True, page_size=CONTROL_PAGE_SIZE, max_rows=CONTROL_MAX_ROWS,
                max_pages=CONTROL_MAX_PAGES, require_complete=True,
                # The owner async read pool is dashboard-facing and may be
                # saturated while this write repair runs.  Provider selection
                # remains explicit and fail-closed on response completeness;
                # a stale circuit read must not turn into an empty blocker.
                blocked_provider_keys=set(),
            )
            rows = valid_rows(api_name, result.rows, trade_date, parse_date)
            if api_name != "suspend_d" and len(rows) < max(1, int(expected * 0.95)):
                raise ValueError(f"{api_name} returned {len(rows)} valid rows; expected at least 95% of {expected}")
            results[api_name] = result
            rows_by_api[api_name] = rows
    except executor_saturated_error as error:
        request_key = hashlib.sha256(json.dumps({"capability": "daily_controls_all_a", "trade_date": stamp}, sort_keys=True).encode()).hexdigest()
        await run_database_blocking(persist_blocked, request_key, error)
        return {"status": "blocked", "trade_date": str(trade_date), "reason": safe_error_detail(str(error), 500)}
    except Exception as error:  # provider result is intentionally not promoted partially
        return {"status": "blocked", "trade_date": str(trade_date), "reason": safe_error_detail(str(error), 500)}

    # The session's ST list is dated evidence for point-in-time research
    # (point_in_time_status).  It is best-effort: a failure leaves the date
    # uncovered, which readers report as the current-flag fallback, and never
    # blocks the four controls above.
    st_rows: list[dict[str, Any]] = []
    st_provider: str | None = None
    st_status = "unavailable"
    try:
        st_result = await call_tushare_api(
            "stock_st", {"trade_date": stamp}, None, "auto",
            paginate=True, page_size=CONTROL_PAGE_SIZE, max_rows=CONTROL_MAX_ROWS,
            max_pages=CONTROL_MAX_PAGES, require_complete=True,
        )
        st_rows = valid_rows("stock_st", st_result.rows, trade_date, parse_date)
        st_provider = str(st_result.provider.key)
        st_status = "captured" if st_rows else "empty_not_recorded"
    except executor_saturated_error:
        st_status = "deferred_executor_saturated"
    except Exception as error:  # noqa: BLE001 - evidence only, never blocks controls
        st_status = f"unavailable: {safe_error_detail(str(error), 200)}"

    observed_at = datetime.now(timezone.utc)
    latency_ms = round((asyncio.get_running_loop().time() - started) * 1000)

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
                result = results[api_name]
                if api_name in persisted_control_apis:
                    normalized[api_name] = len(rows_by_api[api_name])
                    continue
                request_key = hashlib.sha256(json.dumps({"capability": f"{api_name}_all_a", "trade_date": stamp, "provider": result.provider.key}, sort_keys=True).encode()).hexdigest()
                normalized[api_name] = persist_tushare_rows(
                    connection, api_name, request_key, rows_by_api[api_name], result.provider.key, observed_at,
                )
                record_provider_success(connection, result.provider.key, f"{api_name}_all_a", len(rows_by_api[api_name]), latency_ms)
                record_provider_api_capability(
                    connection, result.provider.key, api_name, "verified", len(rows_by_api[api_name]),
                    "Full-market same-day daily control plane refreshed.",
                )
                for provider_key, provider_error in result.failed_providers:
                    record_provider_failure(connection, provider_key, api_name, provider_error, latency_ms)
                    record_provider_api_capability(connection, provider_key, api_name, "failed", note=provider_error)
            # Normalization promotes controls directly to canonical bars.  The
            # source bar table is also a strategy/recovery input, so mirror
            # the verified same-provider controls there rather than leaving
            # its current date with NULLs.
            def selected_provider(api_name: str) -> str:
                result = results[api_name]
                if api_name in persisted_control_apis:
                    if api_name == "adj_factor":
                        return "owner_persisted_adjustment_factor"
                    return str(result.get("provider") or "owner_persisted")
                return str(result.provider.key)

            connection.execute(
                f"""UPDATE quant.market_bars_daily bar
                     SET adj_factor=selected_factor.adj_factor
                     FROM LATERAL (
                           SELECT factor.adj_factor
                             FROM quant.daily_adjustment_factors factor
                            WHERE factor.trading_date=bar.trading_date
                              AND factor.symbol=bar.symbol
                              AND {persisted_factor_semantics_sql('factor')}
                              AND factor.adj_factor>0
                              AND factor.available_at<((bar.trading_date+1)::timestamp AT TIME ZONE 'Asia/Shanghai')
                            ORDER BY array_position(%s::text[],factor.provider) NULLS LAST,
                                     factor.available_at DESC,
                                     factor.provider
                            LIMIT 1
                     ) selected_factor
                    WHERE bar.trading_date=%s""",
                (list(FACTOR_PROVIDER_ORDER), trade_date),
            )
            connection.execute(
                """UPDATE quant.market_bars_daily bar SET limit_up=limits.limit_up,limit_down=limits.limit_down
                     FROM quant.daily_trade_limits limits
                    WHERE bar.trading_date=%s AND limits.trading_date=bar.trading_date
                      AND limits.symbol=bar.symbol AND limits.provider=%s""",
                (trade_date, selected_provider("stk_limit")),
            )
            connection.execute(
                """UPDATE quant.market_bars_daily bar SET is_suspended=true
                     FROM quant.security_suspensions suspension
                    WHERE bar.trading_date=%s AND suspension.suspend_date=%s
                      AND suspension.symbol=bar.symbol AND suspension.provider=%s""",
                (trade_date, trade_date, selected_provider("suspend_d")),
            )
            if st_rows and st_provider:
                connection.execute(
                    """INSERT INTO quant.instrument_lifecycle_evidence(
                           symbol,provider,observed_at,status_date,list_status,is_st,available_at,raw)
                       SELECT candidate.symbol,%s,%s,%s,'UNKNOWN',true,%s,candidate.raw
                         FROM jsonb_to_recordset(%s::jsonb) AS candidate(symbol text, raw jsonb)
                         JOIN quant.instruments instrument ON instrument.symbol=candidate.symbol
                       ON CONFLICT(symbol,provider,status_date,list_status) DO UPDATE SET
                         is_st=true,raw=EXCLUDED.raw""",
                    (st_provider, observed_at, trade_date, observed_at,
                     json.dumps([{"symbol": str(row["ts_code"]).upper(), "raw": row} for row in st_rows],
                                default=str, ensure_ascii=False)),
                )
                normalized["stock_st"] = len(st_rows)
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
        "providers": {
            api_name: (
                "owner_persisted_adjustment_factor"
                if api_name == "adj_factor" and api_name in persisted_control_apis
                else str(result.get("provider") or "owner_persisted")
                if api_name in persisted_control_apis
                else result.provider.key
            )
            for api_name, result in results.items()
        },
        "st_evidence": {"status": st_status, "rows": len(st_rows), "provider": st_provider},
    }


__all__ = [
    "CONTROL_APIS", "CONTROL_PERSIST_TIMEOUT_SECONDS", "CONTROL_PROVIDER_PREFERENCE", "sync", "valid_rows",
]
