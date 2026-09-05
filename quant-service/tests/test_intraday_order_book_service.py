from __future__ import annotations

import asyncio
import os
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.intraday_order_book_service import capture_snapshot, enabled, interval_seconds, max_symbols, persist_observations, retention_days


class IntradayOrderBookServiceTests(unittest.TestCase):
    def test_environment_bounds_are_explicit_and_safe(self) -> None:
        self.assertFalse(enabled({"INTRADAY_ORDER_BOOK_ENABLED": "off"}))
        self.assertTrue(enabled({"INTRADAY_ORDER_BOOK_ENABLED": "yes"}))
        self.assertEqual(interval_seconds({"INTRADAY_ORDER_BOOK_INTERVAL_SECONDS": "0.2"}), 3.0)
        self.assertEqual(interval_seconds({"INTRADAY_ORDER_BOOK_INTERVAL_SECONDS": "bad"}), 3.0)
        self.assertEqual(retention_days({"INTRADAY_ORDER_BOOK_RETENTION_DAYS": "100"}), 30)
        self.assertEqual(max_symbols({"INTRADAY_ORDER_BOOK_MAX_SYMBOLS": "1000"}), 80)

    def test_capture_filters_symbols_and_persists_one_bounded_batch(self) -> None:
        async def check() -> tuple[dict[str, object], list[object]]:
            observed: list[object] = []

            async def fetch(symbols, *, max_symbols):
                observed.extend([symbols, max_symbols])
                return [{"ts_code": symbols[0], "price": 10}]

            def persist(*args):
                observed.append(args)
                return 1

            def persist_error(*args):
                raise AssertionError(f"unexpected error persistence: {args}")

            async def run_database(operation, *args):
                return operation(*args)

            result = await capture_snapshot(
                ["000001.sz", "invalid", "000001.SZ", "600000.SH"], max_symbols_value=1,
                fetch_quotes=fetch, persist=persist, persist_error=persist_error,
                run_database=run_database, safe_error=lambda message, _limit: message,
                handled_errors=(RuntimeError,),
            )
            return result, observed

        result, observed = asyncio.run(check())
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["requested"], 1)
        self.assertEqual(result["stored"], 1)
        self.assertEqual(observed[0], ["000001.SZ"])
        self.assertEqual(observed[1], 1)

    def test_capture_returns_auditable_failure_without_raising(self) -> None:
        async def check() -> tuple[dict[str, object], list[object]]:
            persisted: list[object] = []

            async def fetch(*_args, **_kwargs):
                raise RuntimeError("provider timeout")

            async def run_database(operation, *args):
                operation(*args)

            result = await capture_snapshot(
                ["000001.SZ"], max_symbols_value=40, fetch_quotes=fetch,
                persist=lambda *_args: 0, persist_error=lambda *args: persisted.append(args),
                run_database=run_database, safe_error=lambda message, _limit: f"safe:{message}",
                handled_errors=(RuntimeError,),
            )
            return result, persisted

        result, persisted = asyncio.run(check())
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["reason"], "safe:provider timeout")
        self.assertEqual(len(persisted), 1)

    def test_service_owns_no_fastapi_or_provider_client(self) -> None:
        source = (Path(__file__).resolve().parents[1] / "app" / "intraday_order_book_service.py").read_text(encoding="utf-8")
        self.assertNotIn("from .main", source)
        self.assertNotIn("httpx", source)
        self.assertIn("def persist_observations", source)

    def test_previous_frames_are_selected_per_source_for_mixed_batch(self) -> None:
        """Longhu and Tencent frames must never share an OFI predecessor."""
        class Result:
            def __init__(self, rows=None, rowcount=0):
                self._rows = rows or []
                self.rowcount = rowcount

            def fetchall(self):
                return self._rows

        class Connection:
            def __init__(self, previous):
                self.previous = previous
                self.queries = []
                self.insert_params = []

            def execute(self, query, params=()):
                self.queries.append((query, params))
                if query.lstrip().startswith("SELECT DISTINCT"):
                    return Result(self.previous)
                self.insert_params.append(params)
                return Result(rowcount=2)

        class Database:
            def __init__(self, connection):
                self.connection = connection

            def transaction(self):
                class Transaction:
                    def __enter__(_self):
                        return self.connection

                    def __exit__(_self, *_args):
                        return False

                return Transaction()

        observed_at = datetime(2026, 9, 5, 2, 0, tzinfo=timezone.utc)
        prior_time = observed_at - timedelta(seconds=3)
        previous_longhu = {
            "symbol": "000001.SZ", "source_name": "longhu_order_book", "observed_at": prior_time,
            "raw": {"bids": [{"price": 10, "size": 100}], "asks": [{"price": 10.1, "size": 100}],
                     "cumulative_volume_lot": 1000, "cumulative_amount": 100000},
        }
        previous_tencent = {
            "symbol": "600000.SH", "source_name": "tencent_order_book", "observed_at": prior_time,
            "raw": {"bids": [{"price": 10, "size": 100}], "asks": [{"price": 10.1, "size": 100}],
                     "cumulative_volume_lot": 1000, "cumulative_amount": 100000},
        }
        connection = Connection([previous_longhu, previous_tencent])
        database = Database(connection)
        rows = [
            {"ts_code": "000001.SZ", "source": "longhu_order_book", "price": 10.0, "pre_close": 10.0,
             "bids": [{"price": 10, "size": 120}], "asks": [{"price": 10.1, "size": 90}],
             "cumulative_volume_lot": 1010, "cumulative_amount": 101000},
            {"ts_code": "600000.SH", "source": "tencent_order_book", "price": 10.0, "pre_close": 10.0,
             "bids": [{"price": 10, "size": 120}], "asks": [{"price": 10.1, "size": 90}],
             "cumulative_volume_lot": 1010, "cumulative_amount": 101000},
        ]
        stored = persist_observations(
            database, observed_at, rows, 10, json_safe=lambda value: value,
            record_success=lambda *_args: None,
        )
        self.assertEqual(stored, 2)
        select_query, select_params = connection.queries[0]
        self.assertIn("JOIN unnest(%s::text[],%s::text[])", select_query)
        self.assertEqual(select_params[0], ["000001.SZ", "600000.SH"])
        self.assertEqual(select_params[1], ["longhu_order_book", "tencent_order_book"])
        params = connection.insert_params[0]
        longhu_raw = params[5].obj
        tencent_raw = params[11].obj
        self.assertEqual(longhu_raw["order_book_features"]["delta_status"], "ready")
        self.assertEqual(tencent_raw["order_book_features"]["delta_status"], "ready")

    def test_source_switch_starts_a_fresh_delta_window(self) -> None:
        class Result:
            rowcount = 1

            def __init__(self, rows=None):
                self.rows = rows or []

            def fetchall(self):
                return self.rows

        class Connection:
            def __init__(self):
                self.insert_params = None

            def execute(self, query, params=()):
                if query.lstrip().startswith("SELECT DISTINCT"):
                    return Result([{
                        "symbol": "000001.SZ", "source_name": "longhu_order_book",
                        "observed_at": datetime(2026, 9, 5, 1, 59, 57, tzinfo=timezone.utc),
                        "raw": {"bids": [{"price": 10, "size": 100}], "asks": [{"price": 10.1, "size": 100}],
                                "cumulative_volume_lot": 1000, "cumulative_amount": 100000},
                    }])
                self.insert_params = params
                return Result()

        class Database:
            def __init__(self):
                self.connection = Connection()

            def transaction(self):
                class Transaction:
                    def __enter__(_self): return self.connection
                    def __exit__(_self, *_args): return False
                return Transaction()

        database = Database()
        persist_observations(
            database, datetime(2026, 9, 5, 2, 0, tzinfo=timezone.utc),
            [{"ts_code": "000001.SZ", "source": "tencent_order_book", "price": 10.0, "pre_close": 10.0,
              "bids": [{"price": 10, "size": 120}], "asks": [{"price": 10.1, "size": 90}],
              "cumulative_volume_lot": 1010, "cumulative_amount": 101000}],
            10, json_safe=lambda value: value, record_success=lambda *_args: None,
        )
        features = database.connection.insert_params[5].obj["order_book_features"]
        self.assertEqual(features["delta_status"], "first_snapshot")


@unittest.skipUnless(os.getenv("PGHOST"), "requires the compose PostgreSQL service")
class BatchedPersistenceSqlIntegrationTests(unittest.TestCase):
    """The per-row INSERT loop was replaced with one batched multi-row INSERT."""

    symbols = ["999992.SZ", "999991.SZ"]

    def _cleanup(self) -> None:
        from app.main import db
        with db.transaction() as connection:
            connection.execute("DELETE FROM quant.intraday_quote_observations WHERE symbol=ANY(%s)", (self.symbols,))
            connection.execute("DELETE FROM quant.instruments WHERE symbol=ANY(%s)", (self.symbols,))

    def test_batched_insert_dedupes_and_reports_an_accurate_stored_count(self) -> None:
        from app.main import db, record_provider_success, strategy_json_safe
        self._cleanup()
        observed_at = datetime(2099, 1, 2, 1, 30, tzinfo=timezone.utc)
        try:
            with db.transaction() as connection:
                for symbol in self.symbols:
                    connection.execute(
                        "INSERT INTO quant.instruments(symbol,exchange) VALUES(%s,'SZ') ON CONFLICT DO NOTHING", (symbol,),
                    )
            rows = [{"ts_code": symbol, "price": 10.0 + index, "pre_close": 10.0, "bid1": 10.0, "ask1": 10.1}
                    for index, symbol in enumerate(self.symbols)]
            stored_first = persist_observations(
                db, observed_at, rows, 50, json_safe=strategy_json_safe, record_success=record_provider_success,
            )
            self.assertEqual(stored_first, len(self.symbols))
            with db.transaction() as connection:
                persisted = connection.execute(
                    "SELECT symbol,price FROM quant.intraday_quote_observations WHERE symbol=ANY(%s) ORDER BY symbol",
                    (self.symbols,),
                ).fetchall()
            self.assertEqual({row["symbol"] for row in persisted}, set(self.symbols))
            # A second call at the exact same observed_at collides with the unique
            # (symbol,source_name,observed_at) index for both rows; the batched
            # INSERT's rowcount must reflect that nothing new was actually stored.
            stored_second = persist_observations(
                db, observed_at, rows, 50, json_safe=strategy_json_safe, record_success=record_provider_success,
            )
            self.assertEqual(stored_second, 0)
            # A later observed_at must be free to insert again (no dedup false positive).
            stored_third = persist_observations(
                db, observed_at + timedelta(seconds=30), rows, 50,
                json_safe=strategy_json_safe, record_success=record_provider_success,
            )
            self.assertEqual(stored_third, len(self.symbols))
        finally:
            self._cleanup()


if __name__ == "__main__":
    unittest.main()
