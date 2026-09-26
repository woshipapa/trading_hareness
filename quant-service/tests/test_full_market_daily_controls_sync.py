from __future__ import annotations

import unittest
from datetime import date
from types import SimpleNamespace

from pydantic import ValidationError

from app.full_market_daily_controls_sync import CONTROL_PERSIST_TIMEOUT_SECONDS, sync, valid_rows
from app.request_models import FullMarketDailyControlsSyncRequest


class FullMarketDailyControlsSyncTests(unittest.IsolatedAsyncioTestCase):
    def test_repair_contract_requires_one_explicit_trade_date(self):
        self.assertEqual(
            FullMarketDailyControlsSyncRequest(trade_date="2026-08-18").trade_date,
            date(2026, 8, 18),
        )
        with self.assertRaises(ValidationError):
            FullMarketDailyControlsSyncRequest()

    def test_valid_rows_is_exact_date_and_a_share_only(self):
        trade_date = date(2026, 8, 21)
        rows = valid_rows("adj_factor", [
            {"ts_code": "000001.SZ", "trade_date": "20260821", "adj_factor": 1},
            {"ts_code": "000001.SZ", "trade_date": "20260821", "adj_factor": 2},
            {"ts_code": "000300.SH", "trade_date": "20260820", "adj_factor": 3},
            {"ts_code": "000001.SH", "trade_date": "20260821", "adj_factor": 4},
            {"ts_code": "510300.SH", "trade_date": "20260821", "adj_factor": 5},
            {"ts_code": "900901.SH", "trade_date": "20260821", "adj_factor": 6},
            {"ts_code": "920819.BJ", "trade_date": "20260821", "adj_factor": 7},
            {"ts_code": "302132.SZ", "trade_date": "20260821", "adj_factor": 8},
        ], trade_date, lambda value: date.fromisoformat(f"{value[:4]}-{value[4:6]}-{value[6:8]}"))
        # The SSE composite index, an ETF and a B share do not count toward
        # the equity coverage gate.
        self.assertEqual(rows, [
            {"ts_code": "000001.SZ", "trade_date": "20260821", "adj_factor": 2},
            {"ts_code": "920819.BJ", "trade_date": "20260821", "adj_factor": 7},
            {"ts_code": "302132.SZ", "trade_date": "20260821", "adj_factor": 8},
        ])

    async def test_no_daily_cross_section_blocks_without_provider_calls(self):
        called = False

        async def run_db(action, *args, **_kwargs):
            return action(*args)

        async def fetch(*_args, **_kwargs):
            nonlocal called
            called = True
            raise AssertionError("provider must not be called")

        result = await sync(
            date(2026, 8, 21), expected_daily_rows=lambda _day: 0, call_tushare_api=fetch,
            parse_date=lambda _value: None, persist_tushare_rows=lambda *_args: 0,
            persist_blocked=lambda *_args: None, run_database_blocking=run_db, db=object(),
            safe_error_detail=lambda value, _limit: value, executor_saturated_error=RuntimeError,
            record_provider_success=lambda *_args: None, record_provider_failure=lambda *_args: None,
            record_provider_api_capability=lambda *_args, **_kwargs: None,
        )
        self.assertEqual(result["status"], "blocked")
        self.assertFalse(called)

    async def test_complete_controls_reset_suspension_then_promote_all_apis(self):
        trade_date = date(2026, 8, 21)
        requested: list[str] = []
        persisted: list[str] = []
        statements: list[str] = []
        database_timeouts: list[int | None] = []

        class Connection:
            def execute(self, statement, *_args):
                statements.append(" ".join(statement.split()))

        class Database:
            def transaction(self):
                class Context:
                    def __enter__(self): return Connection()
                    def __exit__(self, *_args): return False
                return Context()

        async def run_db(action, *args, **kwargs):
            database_timeouts.append(kwargs.get("timeout_seconds"))
            return action(*args)

        paging: list[dict] = []

        async def fetch(api_name, _params, _fields, _provider, **kwargs):
            requested.append(api_name)
            paging.append(kwargs)
            rows = [] if api_name == "suspend_d" else [{"ts_code": "000001.SZ", "trade_date": "20260821"}]
            return SimpleNamespace(rows=rows, provider=SimpleNamespace(key="super"), failed_providers=())

        def parse(value):
            return date.fromisoformat(f"{str(value)[:4]}-{str(value)[4:6]}-{str(value)[6:8]}")

        def persist(_connection, api_name, *_args):
            persisted.append(api_name)
            return 1

        result = await sync(
            trade_date, expected_daily_rows=lambda _day: 1, call_tushare_api=fetch, parse_date=parse,
            persist_tushare_rows=persist, persist_blocked=lambda *_args: None, run_database_blocking=run_db,
            db=Database(), safe_error_detail=lambda value, _limit: value, executor_saturated_error=RuntimeError,
            record_provider_success=lambda *_args: None, record_provider_failure=lambda *_args: None,
            record_provider_api_capability=lambda *_args, **_kwargs: None,
        )
        self.assertEqual(result["status"], "completed")
        self.assertEqual(requested, ["adj_factor", "daily_basic", "stk_limit", "suspend_d", "stock_st"])
        self.assertEqual(persisted, requested[:4])
        # The session's ST list is recorded as dated evidence for PIT reads.
        self.assertEqual(result["st_evidence"], {"status": "captured", "rows": 1, "provider": "super"})
        self.assertTrue(any("INSERT INTO quant.instrument_lifecycle_evidence" in statement for statement in statements))
        # Every whole-market control call pages and refuses a short table.
        self.assertTrue(all(call.get("paginate") and call.get("require_complete") for call in paging))
        self.assertEqual(database_timeouts, [None, CONTROL_PERSIST_TIMEOUT_SECONDS])
        self.assertIn("UPDATE quant.canonical_bars_daily SET is_suspended=false,canonicalized_at=now() WHERE trading_date=%s", statements)
        self.assertIn("UPDATE quant.market_bars_daily SET is_suspended=false WHERE trading_date=%s", statements)

    async def test_a_failed_st_list_never_blocks_the_controls(self):
        trade_date = date(2026, 8, 21)

        class Connection:
            def execute(self, statement, *_args):
                return None

        class Database:
            def transaction(self):
                class Context:
                    def __enter__(self): return Connection()
                    def __exit__(self, *_args): return False
                return Context()

        async def run_db(action, *args, **_kwargs):
            return action(*args)

        async def fetch(api_name, _params, _fields, _provider, **_kwargs):
            if api_name == "stock_st":
                raise RuntimeError("stock_st refused")
            rows = [] if api_name == "suspend_d" else [{"ts_code": "000001.SZ", "trade_date": "20260821"}]
            return SimpleNamespace(rows=rows, provider=SimpleNamespace(key="super"), failed_providers=())

        result = await sync(
            trade_date, expected_daily_rows=lambda _day: 1, call_tushare_api=fetch,
            parse_date=lambda value: date.fromisoformat(f"{str(value)[:4]}-{str(value)[4:6]}-{str(value)[6:8]}"),
            persist_tushare_rows=lambda *_args: 1, persist_blocked=lambda *_args: None, run_database_blocking=run_db,
            db=Database(), safe_error_detail=lambda value, _limit: value, executor_saturated_error=TimeoutError,
            record_provider_success=lambda *_args: None, record_provider_failure=lambda *_args: None,
            record_provider_api_capability=lambda *_args, **_kwargs: None,
        )
        self.assertEqual(result["status"], "completed")
        self.assertTrue(result["st_evidence"]["status"].startswith("unavailable"))


if __name__ == "__main__":
    unittest.main()
