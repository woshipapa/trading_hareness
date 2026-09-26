from __future__ import annotations

import unittest
from datetime import date

from app.replay_readiness_coverage import (
    COVERAGE_DEFINITION,
    MATERIALIZED_DAILY_METRICS_SQL,
    REFRESH_DAILY_COVERAGE_SQL,
    refresh_daily_coverage,
)


class ReplayReadinessCoverageTests(unittest.TestCase):
    def test_projection_keeps_point_in_time_controls_and_fail_closed_threshold(self) -> None:
        self.assertIn("universe_membership_history", REFRESH_DAILY_COVERAGE_SQL)
        self.assertIn("fundamentals.available_at <", REFRESH_DAILY_COVERAGE_SQL)
        self.assertIn("limits.available_at <", REFRESH_DAILY_COVERAGE_SQL)
        self.assertIn("adj_factor>0", REFRESH_DAILY_COVERAGE_SQL)
        self.assertIn("greatest(ceil", REFRESH_DAILY_COVERAGE_SQL)
        self.assertIn("is_full_cross_section", MATERIALIZED_DAILY_METRICS_SQL)
        self.assertIn("coverage_definition='" + COVERAGE_DEFINITION + "'", MATERIALIZED_DAILY_METRICS_SQL)
        self.assertIn("point_in_time", COVERAGE_DEFINITION)
        self.assertIn("complete_adjusted", COVERAGE_DEFINITION)

    def test_refresh_replaces_only_the_requested_date_slice(self) -> None:
        class Result:
            rowcount = 1
            def fetchone(self):
                return {"full_cross_section_days": 1}

        class Connection:
            def __init__(self): self.calls = []
            def execute(self, sql, params=()):
                self.calls.append((sql, params))
                return Result()

        connection = Connection()
        result = refresh_daily_coverage(connection, date(2026, 9, 18), date(2026, 9, 18))
        self.assertEqual(result["rows_refreshed"], 1)
        self.assertEqual(len(connection.calls), 3)
        self.assertIn("DELETE FROM quant.replay_readiness_daily_coverage", connection.calls[0][0])
        self.assertEqual(connection.calls[1][1][-1], COVERAGE_DEFINITION)
        self.assertEqual(result["live_effect"], "none")

    def test_refresh_rejects_reversed_range(self) -> None:
        with self.assertRaisesRegex(ValueError, "end_date"):
            refresh_daily_coverage(object(), date(2026, 9, 19), date(2026, 9, 18))


if __name__ == "__main__":
    unittest.main()
