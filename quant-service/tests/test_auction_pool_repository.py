from __future__ import annotations

from datetime import date
import unittest
from unittest.mock import patch

from app.auction_pool_repository import read_pool, read_scored_rows


class Result:
    def __init__(self, rows):
        self.rows = rows

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return self.rows[0] if self.rows else None


class Connection:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def execute(self, sql, params):
        self.calls.append((sql, params))
        return Result(self.rows)


class Database:
    def __init__(self, rows):
        self.connection = Connection(rows)

    def transaction(self):
        class Transaction:
            def __init__(self, connection):
                self.connection = connection

            def __enter__(self):
                return self.connection

            def __exit__(self, *_args):
                return False

        return Transaction(self.connection)


class AuctionPoolRepositoryTests(unittest.TestCase):
    SESSION = date(2026, 10, 13)
    PREVIOUS = date(2026, 10, 12)

    def test_read_pool_orders_sources_deduplicates_and_caps(self):
        database = Database([{"symbol": "000001.SZ"}, {"symbol": "000002.SZ"}])
        reading = {"rows": [
            {"symbol": "000001.SZ", "name": "A", "boards": 2, "risk_rank": 10, "sealed_from_open": True},
            {"symbol": "000002.SZ", "name": "B", "boards": 1, "risk_rank": 20, "sealed_from_open": False},
        ]}
        with patch("app.auction_pool_repository.previous_trading_day", return_value=self.PREVIOUS), \
             patch("app.auction_pool_repository.limit_up_risk_runtime.daily", return_value=reading), \
             patch("app.auction_pool_repository.enabled_watch_symbols", return_value=["000002.SZ", "000004.SZ"]), \
             patch("app.auction_pool_repository.integer", return_value=3):
            self.assertEqual(read_pool(database, self.SESSION), ["000001.SZ", "000002.SZ", "000004.SZ"])

    def test_missing_previous_reading_returns_empty_scored_rows_and_pool(self):
        database = Database([])
        with patch("app.auction_pool_repository.previous_trading_day", return_value=self.PREVIOUS), \
             patch("app.auction_pool_repository.limit_up_risk_runtime.daily", return_value=None), \
             patch("app.auction_pool_repository.enabled_watch_symbols", return_value=[]):
            self.assertEqual(read_scored_rows(database, self.SESSION), [])
            self.assertEqual(read_pool(database, self.SESSION), [])

    def test_scored_rows_keep_only_table_fields_and_order(self):
        database = Database([])
        reading = {"rows": [
            {"symbol": "000002.SZ", "name": "B", "boards": 1, "risk_rank": 20,
             "sealed_from_open": False, "risk": 0.2},
        ]}
        with patch("app.auction_pool_repository.previous_trading_day", return_value=self.PREVIOUS), \
             patch("app.auction_pool_repository.limit_up_risk_runtime.daily", return_value=reading):
            self.assertEqual(read_scored_rows(database, self.SESSION), [{
                "symbol": "000002.SZ", "name": "B", "boards": 1, "risk_rank": 20, "sealed_from_open": False,
            }])

    def test_scored_rows_add_previous_close_from_valid_daily_bar(self):
        database = Database([{"symbol": "000002.SZ", "close": 12.5}])
        reading = {"rows": [{"symbol": "000002.SZ", "name": "B", "boards": 1, "risk_rank": 20,
                             "sealed_from_open": False}]}
        with patch("app.auction_pool_repository.previous_trading_day", return_value=self.PREVIOUS), \
             patch("app.auction_pool_repository.limit_up_risk_runtime.daily", return_value=reading):
            self.assertEqual(read_scored_rows(database, self.SESSION)[0]["previous_close"], 12.5)
            self.assertIn("NOT coalesce(is_suspended, false)", database.connection.calls[-1][0])


if __name__ == "__main__":
    unittest.main()
