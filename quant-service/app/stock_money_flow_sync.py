"""End-of-day per-stock capital flow: what is stored, and whether a session has it.

Per-stock flow is the Longhu close's main net amount (``longhuvip_main_net``),
written by the full-market close through :func:`persist_flow_rows` for about
5,300 names a session.  Until 2026-10-08 this stage also fetched Tushare's
``moneyflow``, ``moneyflow_dc`` and ``moneyflow_ths`` cross-sections; Tushare
was retired then (decision 0005) and those sources' history stays in the
table under their own names.  The vendors' "main"/"large" order buckets are
defined differently, so they were never merged and Longhu's is not presented
as any of them.

The daily pipeline now only checks that the close stored a usable cross-
section for the session, so its report says whether flow exists for the date.

Boundary, stated plainly: this is end-of-day only.  ``signal_rules``'s live
``main_net_inflow`` has no licensed intraday per-stock source, and nothing
here should be read as providing one.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Awaitable, Callable

from psycopg.types.json import Json


#: The close's per-stock flow source; see longhu_market_sync.FLOW_SOURCE.
FLOW_SOURCE = "longhuvip_main_net"
FLOW_PROVIDER = "longhuvip_composite"
#: Below this the close clearly stored a partial cross-section; a partial flow
#: snapshot is worse than none because a missing symbol is silently read as
#: "no flow" by any downstream aggregate.
MINIMUM_COVERAGE_RATIO = 0.80


def persist_flow_rows(connection: Any, rows: list[dict[str, Any]], provider: str,
                      available_at: datetime) -> int:
    """Store one session's flow cross-section in a single batched statement.

    A full-market day is ~5,300 rows and the owner database is an SSH tunnel
    away at 52ms per round trip, so a statement per row spent four and a half
    minutes here alone.  The statement, its guard against an unknown instrument
    and its conflict resolution are unchanged; only the grouping differs.
    """
    if not rows:
        return 0
    with connection.cursor() as cursor:
        cursor.executemany(
            """INSERT INTO quant.stock_money_flow_daily(
                    symbol,trading_date,source,provider,net_amount,net_amount_rate,
                    buy_elg_amount,buy_lg_amount,buy_md_amount,buy_sm_amount,available_at,raw)
               SELECT %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s
                WHERE EXISTS(SELECT 1 FROM quant.instruments WHERE symbol=%s)
               ON CONFLICT(symbol,trading_date,source) DO UPDATE SET
                 provider=EXCLUDED.provider,net_amount=EXCLUDED.net_amount,
                 net_amount_rate=EXCLUDED.net_amount_rate,buy_elg_amount=EXCLUDED.buy_elg_amount,
                 buy_lg_amount=EXCLUDED.buy_lg_amount,buy_md_amount=EXCLUDED.buy_md_amount,
                 buy_sm_amount=EXCLUDED.buy_sm_amount,available_at=EXCLUDED.available_at,
                 raw=EXCLUDED.raw""",
            [(row["symbol"], row["trading_date"], row["source"], provider, row["net_amount"],
              row["net_amount_rate"], row["buy_elg_amount"], row["buy_lg_amount"],
              row["buy_md_amount"], row["buy_sm_amount"], available_at, Json(row["raw"]),
              row["symbol"]) for row in rows],
        )
    return len(rows)


def stored_flow_symbols(connection: Any, trade_date: date) -> int:
    row = connection.execute(
        """SELECT count(DISTINCT symbol)::int AS n FROM quant.stock_money_flow_daily
            WHERE trading_date=%s AND source=%s""",
        (trade_date, FLOW_SOURCE),
    ).fetchone()
    return int((row or {}).get("n") or 0)


async def sync(
    trade_date: date,
    *,
    expected_symbols: Callable[[date], int],
    run_database_blocking: Callable[..., Awaitable[Any]],
    db: Any,
) -> dict[str, Any]:
    """Report whether the close stored one session's per-stock flow, against its universe."""
    expected = await run_database_blocking(expected_symbols, trade_date)
    if expected <= 0:
        return {"status": "blocked", "trade_date": str(trade_date),
                "reason": "no daily bars for this date; flow would have no universe to check against"}

    def count() -> int:
        with db.transaction() as connection:
            return stored_flow_symbols(connection, trade_date)

    stored = await run_database_blocking(count, timeout_seconds=30)
    complete = stored >= int(expected * MINIMUM_COVERAGE_RATIO)
    return {"status": "completed" if complete else "missing", "trade_date": str(trade_date),
            "expected_symbols": expected, "rows": {FLOW_SOURCE: stored},
            "providers": {FLOW_SOURCE: FLOW_PROVIDER},
            "reason": None if complete else (
                f"the close stored flow for {stored} of {expected} symbols; "
                f"below the {MINIMUM_COVERAGE_RATIO:.0%} coverage floor"),
            "boundary": "end_of_day_only; vendor order-size classification, no licensed intraday per-stock flow"}


__all__ = [
    "FLOW_PROVIDER", "FLOW_SOURCE", "MINIMUM_COVERAGE_RATIO", "persist_flow_rows", "stored_flow_symbols", "sync",
]
