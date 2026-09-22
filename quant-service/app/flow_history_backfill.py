"""Resumable history backfill: per-stock money flow and the daily limit-up list.

Money flow existed for the full market only from 2026-08-21 (Longhu) and the
limit-up pools only from 2026-09-07, so flow factors and the limit-up
strategy family could not be tested on the 3-year daily history.  This walks
the SSE calendar one session at a time:

* ``moneyflow_dc`` through the same normalizer, coverage gate and batched
  write as the nightly sync (``stock_money_flow_sync``);
* ``limit_list_d`` through the ordinary Tushare fetch path, which keeps the
  rows in ``tushare_raw_records`` for research.

A session that already has a covering cross-section is skipped, so a run can
stop and resume anywhere.  Calls are paced; provider rate limits and circuit
breakers still apply underneath.  Research data only.

    python -m app.flow_history_backfill 2023-09-01 2026-08-20 [--limit-list]
"""

from __future__ import annotations

import argparse
import asyncio
import time
from datetime import date, datetime, timezone
from typing import Any, Awaitable, Callable

from .stock_money_flow_sync import MINIMUM_COVERAGE_RATIO, normalize_flow_rows, persist_flow_rows

FLOW_API = "moneyflow_dc"


def sessions_between(connection: Any, start: date, end: date) -> list[date]:
    return [row["calendar_date"] for row in connection.execute(
        """SELECT calendar_date FROM quant.market_trade_calendar WHERE exchange='SSE' AND is_open
            AND calendar_date BETWEEN %s AND %s ORDER BY calendar_date DESC""", (start, end)).fetchall()]


def flow_covered(connection: Any, trade_date: date, expected: int) -> bool:
    row = connection.execute(
        "SELECT count(*) AS n FROM quant.stock_money_flow_daily WHERE trading_date=%s AND source=%s",
        (trade_date, FLOW_API)).fetchone()
    return int(row["n"] or 0) >= int(expected * MINIMUM_COVERAGE_RATIO)


def limit_list_stored(connection: Any, trade_date: date) -> bool:
    row = connection.execute(
        """SELECT 1 FROM quant.tushare_raw_records WHERE api_name='limit_list_d'
            AND row_data->>'trade_date'=%s LIMIT 1""", (trade_date.strftime("%Y%m%d"),)).fetchone()
    return row is not None


async def backfill(start: date, end: date, *, database: Any, run_database: Callable[..., Awaitable[Any]],
                   call_tushare_api: Callable[..., Awaitable[Any]], expected_symbols: Callable[[date], int],
                   parse_date: Callable[[Any], date | None],
                   fetch_limit_list: Callable[[date], Awaitable[Any]] | None = None,
                   pace_seconds: float = 1.2, log: Callable[[str], None] = print) -> dict[str, Any]:
    def read_sessions() -> list[date]:
        with database.transaction() as connection:
            return sessions_between(connection, start, end)

    report = {"sessions": 0, "flow_stored": 0, "flow_skipped": 0, "flow_blocked": 0, "limit_list_stored": 0,
              "limit_list_skipped": 0, "errors": {}}
    for trade_date in await run_database(read_sessions, timeout_seconds=60):
        report["sessions"] += 1
        expected = await run_database(expected_symbols, trade_date, timeout_seconds=60)

        def covered() -> tuple[bool, bool]:
            with database.transaction() as connection:
                return (flow_covered(connection, trade_date, expected),
                        fetch_limit_list is None or limit_list_stored(connection, trade_date))

        flow_done, limit_done = await run_database(covered, timeout_seconds=60)
        if flow_done:
            report["flow_skipped"] += 1
        elif expected <= 0:
            report["flow_blocked"] += 1
        else:
            try:
                result = await call_tushare_api(FLOW_API, {"trade_date": trade_date.strftime("%Y%m%d")}, None, "auto")
                rows = normalize_flow_rows(FLOW_API, result.rows, trade_date, parse_date)
                if len(rows) < int(expected * MINIMUM_COVERAGE_RATIO):
                    report["flow_blocked"] += 1
                    report["errors"][str(trade_date)] = f"{FLOW_API}: {len(rows)} rows for {expected} symbols"
                else:
                    observed_at = datetime.now(timezone.utc)

                    def persist() -> int:
                        with database.transaction() as connection:
                            return persist_flow_rows(connection, rows, result.provider.key, observed_at)

                    report["flow_stored"] += await run_database(persist, timeout_seconds=180)
            except Exception as error:  # noqa: BLE001 - one session never stops the walk
                report["errors"][str(trade_date)] = f"{FLOW_API}: {str(error)[:200]}"
            await asyncio.sleep(pace_seconds)
        if fetch_limit_list is not None:
            if limit_done:
                report["limit_list_skipped"] += 1
            else:
                try:
                    await fetch_limit_list(trade_date)
                    report["limit_list_stored"] += 1
                except Exception as error:  # noqa: BLE001
                    report["errors"][f"{trade_date}:limit_list"] = str(error)[:200]
                await asyncio.sleep(pace_seconds)
        if report["sessions"] % 20 == 0:
            log(f"flow backfill at {trade_date}: {report['sessions']} sessions, {report['flow_stored']} flow rows, "
                f"{report['limit_list_stored']} limit lists, {len(report['errors'])} errors")
    return report


def main() -> None:  # pragma: no cover - operational entry point
    from . import main as service
    from .request_models import TushareFetchRequest

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("start", type=date.fromisoformat)
    parser.add_argument("end", type=date.fromisoformat)
    parser.add_argument("--limit-list", action="store_true", help="also store limit_list_d per session")
    parser.add_argument("--pace", type=float, default=1.2)
    args = parser.parse_args()

    async def fetch_limit_list(trade_date: date) -> Any:
        return await service.fetch_tushare_catalog(TushareFetchRequest(
            api_name="limit_list_d", params={"trade_date": trade_date.strftime("%Y%m%d")},
            paginate=True, page_size=1000, max_rows=3000, force_refresh=True))

    started = time.monotonic()
    report = asyncio.run(backfill(
        args.start, args.end, database=service.db, run_database=service.run_database_blocking,
        call_tushare_api=service.call_tushare_api, expected_symbols=service.full_market_daily_row_count,
        parse_date=service.tushare_date, fetch_limit_list=fetch_limit_list if args.limit_list else None,
        pace_seconds=args.pace))
    report["seconds"] = round(time.monotonic() - started, 1)
    print(report)


if __name__ == "__main__":  # pragma: no cover
    main()


__all__ = ["FLOW_API", "backfill", "flow_covered", "limit_list_stored", "sessions_between"]
