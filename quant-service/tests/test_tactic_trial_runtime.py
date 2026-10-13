import unittest
from datetime import date
from unittest import mock

from app import tactic_trial_runtime as runtime


class Database:
    def __init__(self):
        self.stores = []

    def transaction(self):
        database = self
        class Transaction:
            def __enter__(self): return database
            def __exit__(self, *_args): return False
        return Transaction()


class TacticTrialRuntimeTests(unittest.TestCase):
    def test_refresh_close_written_once_and_blocked_without_inputs(self):
        database = Database()
        day = date(2026, 10, 13)
        with mock.patch.object(runtime.tactic_trial_repository, "read_capability", side_effect=[[], []]), \
             mock.patch.object(runtime.tactic_trial_repository, "read_limit_lists", return_value={"days": []}):
            self.assertEqual(runtime.refresh_close(database, day, apply=False)["status"], "blocked")

        existing = {"trade_date": day.isoformat(), "version": runtime.VERSION, "rows": [{"symbol": "A"}]}
        with mock.patch.object(runtime.tactic_trial_repository, "read_capability", return_value=[existing]):
            result = runtime.refresh_close(database, day, apply=False)
        self.assertEqual(result["status"], "already_stored")
        self.assertTrue(result["research_only"])

    def test_refresh_auction_builds_unknown_prev_row(self):
        database = Database()
        day = date(2026, 10, 13)
        desk = {"trade_date": day.isoformat(), "rows": [{
            "symbol": "000001.SZ", "name": "A", "open": 10.5, "gap": 0.05,
            "opens_at_limit": False, "bucket": "3-5", "boards": 2, "amount": 100,
        }]}
        with mock.patch.object(runtime.tactic_trial_repository, "read_capability", side_effect=[[], [desk], []]), \
             mock.patch.object(runtime.tactic_trial_repository, "read_open_sessions", return_value=[]), \
             mock.patch.object(runtime.derived_daily_readings, "store", return_value={"stored": 1, "unchanged": 0}):
            result = runtime.refresh_auction(database, day)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["rows"], 1)

    def test_summarise_makes_three_range_readings(self):
        database = Database()
        calls = []
        def readings(connection, capability, first, last):
            calls.append(capability)
            return []
        with mock.patch.object(runtime.tactic_trial_repository, "read_capability", side_effect=readings), \
             mock.patch.object(runtime.derived_daily_readings, "newest", return_value=[]), \
             mock.patch.object(runtime.derived_daily_readings, "store", return_value={"stored": 1, "unchanged": 0}):
            result = runtime.summarise(database, date(2026, 10, 13))
        self.assertEqual(calls, [runtime.CLOSE_CAPABILITY, runtime.AUCTION_CAPABILITY, runtime.SETTLED_CAPABILITY])
        self.assertEqual(result["status"], "completed")

    def test_replay_refuses_a_year_over_the_disagreement_limit(self):
        database = Database()
        with mock.patch.object(runtime.tactic_trial_repository, "read_open_sessions", return_value=[]), \
             mock.patch.object(runtime, "_year_report", return_value={"2026": {"days_built": 1, "rows": 1, "disagreement_share": 0.01, "raw_basis_share": 0.0}}), \
             mock.patch.object(runtime.derived_daily_readings, "store") as store:
            result = runtime.replay(database, "close", date(2026, 1, 1), date(2026, 1, 3), apply=True)
        self.assertEqual(result["status"], "refused")
        store.assert_not_called()


if __name__ == "__main__":
    unittest.main()
