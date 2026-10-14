import unittest
from datetime import date, datetime
from zoneinfo import ZoneInfo

from app.tactic_trial_repository import (
    read_auction_lists, read_auction_table, read_day_bars, read_exit_bars,
    read_history_windows, read_limit_lists, read_market_turnover, read_newest,
    read_open_sessions, read_sessions_after,
)


class Result:
    def __init__(self, rows=()):
        self.rows = list(rows)

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return self.rows[0] if self.rows else None


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
        read_sessions_after(connection, end, 1)
        self.assertLessEqual(len(connection.calls), 10)
        for sql, _params in connection.calls:
            if "raw_market_observations" in sql or "canonical_bars_daily" in sql:
                self.assertRegex(sql, r"(?i)(between|effective_at\s*>=|trading_date\s+between|trading_date>%|trading_date<=|pick_day-)")
        calendar = [params for sql, params in connection.calls if "market_trade_calendar" in sql]
        self.assertTrue(any(len(params) >= 3 for params in calendar))

    def test_the_single_document_reads_ask_for_one_row(self):
        stored_at = datetime(2026, 10, 14, 1, 25, 3, tzinfo=ZoneInfo("UTC"))

        class One(Connection):
            def execute(self, sql, params=()):
                self.calls.append((sql, params))
                return Result([{"available_at": stored_at, "normalized": {
                    "trade_date": "2026-10-14", "version": "auction-open-desk-v1", "rows": [{"symbol": "000001.SZ"}],
                    "provider_key": "local_derived", "capability": "auction_open_desk_daily"}}])

        connection = One()
        self.assertEqual(read_newest(connection, "tactic_trials_summary_daily", date(2026, 10, 14))["trade_date"], "2026-10-14")
        table = read_auction_table(connection, date(2026, 10, 14))
        self.assertEqual(table, {"stored_at": "2026-10-14T09:25:03+08:00", "rows": [{"symbol": "000001.SZ"}]},
                         "the table's time is when it was stored: the table itself carries none")
        self.assertIsNone(read_auction_table(connection, date(2026, 10, 15)), "the newest table of an earlier day is not today's")
        for sql, _params in connection.calls:
            self.assertRegex(sql, r"(?is)effective_at <= %s.*order by effective_at desc, available_at desc limit 1")

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
