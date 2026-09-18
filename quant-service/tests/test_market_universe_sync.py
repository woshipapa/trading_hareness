import asyncio
import unittest
from datetime import date
from types import SimpleNamespace

from app import market_universe_sync


class _Connection:
    def __init__(self):
        self.statements = []

    def execute(self, statement, values=None):
        self.statements.append((" ".join(statement.split()), values))
        return SimpleNamespace(fetchone=lambda: None, fetchall=lambda: [], rowcount=0)


class _Database:
    def __init__(self, connection):
        self.connection = connection

    def transaction(self):
        connection = self.connection

        class _Ctx:
            def __enter__(self_inner):
                return connection

            def __exit__(self_inner, *_args):
                return False

        return _Ctx()


class ProviderCallError(RuntimeError):
    pass


class ExecutorSaturated(RuntimeError):
    pass


def _rows(count):
    return [{"ts_code": f"{600000 + index}.SH", "name": f"n{index}", "list_date": "19910403"}
            for index in range(count)]


class MarketUniverseSyncTests(unittest.TestCase):
    """instruments.list_date comes from here, and the volume baseline needs it."""

    def _run(self, *, rows, provider="auto", minimum_rows=5000):
        seen = {}

        async def call_tushare_api(api_name, params, fields, preference, **kwargs):
            seen["call"] = {"api_name": api_name, "params": params, "fields": fields, **kwargs}
            return SimpleNamespace(rows=rows, provider=SimpleNamespace(key="tushare_super_get"),
                                   failed_providers=())

        async def run_database_blocking(action, *args, **kwargs):
            return action(*args) if args else action()

        connection = _Connection()
        result = asyncio.run(market_universe_sync.sync(
            SimpleNamespace(universe_key="all_a", provider=provider, minimum_rows=minimum_rows),
            provider_candidates=lambda *_args: [SimpleNamespace(key="tushare_super_get")],
            cn_date=lambda: date(2026, 9, 18),
            call_tushare_api=call_tushare_api,
            looks_like_response_header=lambda _rows: False,
            persist_tushare_rows=lambda *_args, **_kwargs: len(rows),
            run_database_blocking=run_database_blocking,
            persist_tushare_fetch_blocked=lambda *_args, **_kwargs: None,
            db=_Database(connection),
            safe_error_detail=lambda text, _limit: text,
            provider_call_error=ProviderCallError,
            executor_saturated_error=ExecutorSaturated,
            record_provider_success=lambda *_args, **_kwargs: None,
            record_provider_failure=lambda *_args, **_kwargs: None,
            record_provider_api_capability=lambda *_args, **_kwargs: None,
        ))
        return result, seen, connection

    def test_the_universe_is_requested_in_pages_and_must_be_complete(self):
        # One response for ~5,600 rows is refused, which is why this capability
        # had never succeeded and every instrument carried a null list_date.
        _result, seen, _connection = self._run(rows=_rows(5565))
        call = seen["call"]
        self.assertEqual(call["api_name"], "stock_basic")
        self.assertTrue(call["paginate"])
        self.assertTrue(call["require_complete"])
        self.assertLessEqual(call["page_size"], 3000)
        self.assertGreaterEqual(call["max_pages"] * call["page_size"], call["max_rows"])

    def test_list_date_is_among_the_requested_fields(self):
        # The market volume baseline separates index series from listed names
        # by this column alone; without it the leader-flow market gate closes.
        _result, seen, _connection = self._run(rows=_rows(5565))
        self.assertIn("list_date", seen["call"]["fields"])

    def test_a_complete_cross_section_is_accepted(self):
        result, _seen, _connection = self._run(rows=_rows(5565))
        self.assertEqual(result["status"], "completed")

    def test_a_short_cross_section_is_reported_blocked_rather_than_stored(self):
        # A partial universe would silently shrink every downstream scan.
        result, _seen, _connection = self._run(rows=_rows(12))
        self.assertEqual(result["status"], "blocked")
        self.assertIn("12 valid active symbols", result["reason"])

    def test_symbols_that_are_not_a_share_codes_are_dropped(self):
        rows = _rows(5565) + [{"ts_code": "HSI", "name": "index", "list_date": ""}]
        result, _seen, _connection = self._run(rows=rows)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["imported"], 5565)


if __name__ == "__main__":
    unittest.main()
