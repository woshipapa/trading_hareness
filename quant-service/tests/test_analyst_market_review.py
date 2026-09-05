from __future__ import annotations

import unittest
from contextlib import contextmanager
from datetime import date

from app.analyst_market_evaluation import analyst_market_evaluation
from app.analyst_market_review import _market_points, ordinary_least_squares


class _Result:
    def fetchall(self):
        return []


class _Connection:
    def __init__(self):
        self.calls = []

    def execute(self, statement, params=()):
        self.calls.append((statement, params))
        return _Result()


class _Database:
    def __init__(self, connection):
        self.connection = connection

    @contextmanager
    def transaction(self):
        yield self.connection


class AnalystMarketReviewTests(unittest.TestCase):
    def test_evaluation_and_review_use_session_available_market_evidence(self):
        connection = _Connection()
        database = _Database(connection)
        analyst_market_evaluation(database, date(2026, 8, 17), date(2026, 8, 21))
        _market_points(database, date(2026, 8, 17), date(2026, 8, 21), {"timeline": []})
        opinion_sql = next(sql for sql, _params in connection.calls if "FROM quant.analyst_opinions" in sql)
        outcome_sql = next(sql for sql, _params in connection.calls if "FROM quant.analyst_opinion_outcomes" in sql)
        flow_sql = [sql for sql, _params in connection.calls if "FROM quant.market_flow_feature_snapshots" in sql]
        sector_sql = next(sql for sql, _params in connection.calls if "FROM quant.sector_flow_daily_features" in sql)
        self.assertIn("available_at<=%s", opinion_sql)
        self.assertIn("p.available_at<=%s", outcome_sql)
        self.assertGreaterEqual(len(flow_sql), 2)
        self.assertTrue(all("status='ready'" in sql and "observed_at<=%s" in sql for sql in flow_sql))
        self.assertIn("feature.status='ready'", sector_sql)
        self.assertIn("feature.available_at<=%s", sector_sql)

    def test_regression_is_explicitly_gated(self):
        result = ordinary_least_squares([{"x": 1, "y": 2}], "x", "y")
        self.assertEqual(result["status"], "insufficient_history")
        self.assertEqual(result["live_effect"], "none")

    def test_regression_reports_coefficients_when_mature(self):
        result = ordinary_least_squares([{"x": value, "y": 2 * value + 1} for value in range(8)], "x", "y")
        self.assertEqual(result["status"], "ready")
        self.assertAlmostEqual(result["slope"], 2.0)
        self.assertAlmostEqual(result["intercept"], 1.0)
        self.assertAlmostEqual(result["r_squared"], 1.0)


if __name__ == "__main__":
    unittest.main()
