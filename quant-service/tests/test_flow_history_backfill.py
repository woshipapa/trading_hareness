"""The flow history backfill walks sessions, skips covered ones and never stops on one failure."""

from __future__ import annotations

import asyncio
import unittest
from datetime import date
from types import SimpleNamespace

from app.flow_history_backfill import backfill


class Result:
    def __init__(self, rows=None, row=None):
        self.rows, self.row = rows or [], row

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return self.row


class FakeDatabase:
    def __init__(self, covered_dates: set[date]):
        self.covered, self.written = covered_dates, []

    def transaction(self):
        database = self

        class Tx:
            def __enter__(self):
                return database

            def __exit__(self, *exc):
                return False
        return Tx()

    def execute(self, sql, params=()):
        if "market_trade_calendar" in sql:
            return Result(rows=[{"calendar_date": d} for d in (date(2025, 1, 3), date(2025, 1, 2), date(2024, 12, 31))])
        if "stock_money_flow_daily" in sql:
            return Result(row={"n": 5000 if params[0] in self.covered else 0})
        if "tushare_raw_records" in sql:
            return Result(row=None)
        raise AssertionError(sql)

    def cursor(self):
        database = self

        class Cursor:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def executemany(self, sql, params):
                database.written.extend(params)
        return Cursor()


class FlowHistoryBackfillTests(unittest.TestCase):
    def test_walks_sessions_skips_covered_and_survives_a_failed_call(self):
        database = FakeDatabase({date(2025, 1, 3)})
        calls, limit_calls = [], []

        async def run_database(fn, *args, timeout_seconds=None):
            return fn(*args)

        async def call_tushare_api(api, params, fields, provider):
            calls.append(params["trade_date"])
            if params["trade_date"] == "20241231":
                raise RuntimeError("provider down")
            rows = [{"ts_code": f"{i:06d}.SZ", "trade_date": params["trade_date"], "net_amount": 1.0} for i in range(10)]
            return SimpleNamespace(rows=rows, provider=SimpleNamespace(key="tushare_super_get"))

        async def fetch_limit_list(trade_date):
            limit_calls.append(trade_date)

        report = asyncio.run(backfill(
            date(2024, 12, 1), date(2025, 1, 3), database=database, run_database=run_database,
            call_tushare_api=call_tushare_api, expected_symbols=lambda _d: 10,
            parse_date=lambda value: date(int(value[:4]), int(value[4:6]), int(value[6:8])),
            fetch_limit_list=fetch_limit_list, pace_seconds=0))
        self.assertEqual(calls, ["20250102", "20241231"])          # 01-03 already covered
        self.assertEqual(report["flow_skipped"], 1)
        self.assertEqual(report["flow_stored"], 10)
        self.assertIn("2024-12-31", report["errors"])
        self.assertEqual(len(limit_calls), 3)
        self.assertEqual(report["sessions"], 3)


class HistoricalCrossSectionTests(unittest.TestCase):
    def test_counts_fresh_and_partial_bars_without_the_next_session_rule(self):
        from app.flow_history_backfill import historical_cross_section
        seen = {}

        class Database:
            def transaction(self):
                class Tx:
                    def __enter__(self_inner):
                        return self_inner

                    def __exit__(self_inner, *exc):
                        return False

                    def execute(self_inner, sql, params):
                        seen["sql"] = sql
                        return Result(row={"n": 5300})
                return Tx()

        self.assertEqual(historical_cross_section(Database(), date(2024, 5, 6)), 5300)
        self.assertIn("quality_status IN ('fresh','partial')", seen["sql"])
        self.assertNotIn("available_at", seen["sql"])


if __name__ == "__main__":
    unittest.main()
