import unittest
from datetime import date, datetime
from zoneinfo import ZoneInfo

from app.tactic_trial_repository import (
    read_auction_lists, read_day_bars, read_exit_bars,
    read_history_windows, read_limit_lists, read_market_turnover, read_next_sessions,
    read_open_sessions,
)


class Result:
    def __init__(self, rows=()):
        self.rows = list(rows)

    def fetchall(self):
        return self.rows


class Connection:
    def __init__(self):
        self.calls = []

    def execute(self, sql, params=()):
        self.calls.append((sql, params))
        if "market_trade_calendar" in sql:
            return Result()
        return Result()


class TacticTrialRepositoryTests(unittest.TestCase):
    def test_range_readers_use_bounded_statement_counts_and_dates(self):
        connection = Connection()
        start, end = date(2025, 1, 1), date(2025, 7, 19)
        read_limit_lists(connection, start, end)
        read_day_bars(connection, start, end, ["000001.SZ"])
        read_history_windows(connection, [("000001.SZ", end)])
        read_market_turnover(connection, start, end)
        read_exit_bars(connection, [("000001.SZ", end)])
        read_auction_lists(connection, start, end)
        read_open_sessions(connection, start, end)
        read_next_sessions(connection, end, 12)
        self.assertLessEqual(len(connection.calls), 10)
        for sql, _params in connection.calls:
            if "raw_market_observations" in sql or "canonical_bars_daily" in sql:
                self.assertRegex(sql, r"(?i)(between|effective_at\s*>=|trading_date\s+between|trading_date>%|trading_date<=|pick_day-)")
        calendar = [params for sql, params in connection.calls if "market_trade_calendar" in sql]
        self.assertTrue(any(len(params) >= 3 for params in calendar))

    def test_auction_rows_keep_order_and_amount_position(self):
        connection = Connection()
        observed = datetime(2026, 1, 2, 7, tzinfo=ZoneInfo("UTC"))
        connection.execute = lambda sql, params=(): Result([{
            "effective_at": observed, "symbol": "auction", "payload": {"rows": [["000001", 1, 2, 3, 500], ["000002", 1, 2, 3, 300]]},
        }])
        result = read_auction_lists(connection, date(2026, 1, 2), date(2026, 1, 2))
        self.assertEqual(result[0]["rows"], [
            {"symbol": "000001.SZ", "amount": 500, "position": 0},
            {"symbol": "000002.SZ", "amount": 300, "position": 1},
        ])

    def test_limit_list_source_rows_are_grouped_by_day_and_list_key(self):
        connection = Connection()
        observed = datetime(2026, 1, 2, 7, tzinfo=ZoneInfo("UTC"))
        connection.execute = lambda sql, params=(): Result([
            {"provider_key": "longhuvip_history", "symbol": "pid:1", "effective_at": observed, "payload": {"rows": [["a"]]}},
            {"provider_key": "longhuvip", "symbol": "pid:2", "effective_at": observed, "payload": {"rows": [["b"]]}},
        ])
        result = read_limit_lists(connection, date(2026, 1, 2), date(2026, 1, 2))
        self.assertEqual(result["days"][0]["source"], "history")
        self.assertEqual(result["days"][0]["lists"]["pid:1"], [["a"]])
        self.assertEqual(result["days"][0]["lists"]["pid:2"], [])

    def test_history_window_is_bounded_by_each_pick_day(self):
        connection = Connection()
        read_history_windows(connection, [("000001.SZ", date(2025, 7, 19))])
        sql, params = connection.calls[-1]
        self.assertIn("b.trading_date BETWEEN w.pick_day - 400 AND w.pick_day", sql)
        self.assertEqual(len(params), 2)

    def test_exit_limits_use_one_latest_limit_row(self):
        connection = Connection()
        read_exit_bars(connection, [("000001.SZ", date(2025, 7, 19))])
        sql, _params = connection.calls[-1]
        self.assertIn("LEFT JOIN LATERAL", sql)
        self.assertIn("ORDER BY available_at DESC LIMIT 1", sql)


if __name__ == "__main__":
    unittest.main()
