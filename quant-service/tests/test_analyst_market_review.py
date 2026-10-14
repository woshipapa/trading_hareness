from __future__ import annotations

import unittest
from contextlib import contextmanager
from datetime import date, timedelta
from unittest.mock import patch

from app.analyst_market_evaluation import analyst_market_evaluation
from app import analyst_market_review
from app.analyst_market_review import (
    MIN_REGRESSION_DAYS, _market_points, _period, _regression_window, ordinary_least_squares,
)


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
        observation_sql = next(sql for sql, _params in connection.calls if "FROM quant.analyst_observations" in sql)
        self.assertIn("AT TIME ZONE 'Asia/Shanghai')::date BETWEEN", observation_sql,
                      "the day of an observation is its Shanghai day, whatever the session's time zone")
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

    def test_a_weekly_period_can_never_reach_the_regression_gate_by_itself(self):
        start, end = _period("weekly", date(2026, 10, 9))
        self.assertLess((end - start).days + 1, MIN_REGRESSION_DAYS * 7 // 5,
                        "seven calendar days hold at most five sessions: below the gate")

    def test_the_weekly_regression_reads_a_trailing_window_long_enough_for_the_gate(self):
        start, end = _regression_window(date(2026, 10, 9))
        self.assertEqual(end, date(2026, 10, 9))
        sessions = sum(1 for offset in range((end - start).days + 1)
                       if (start + timedelta(days=offset)).weekday() < 5)
        self.assertGreaterEqual(sessions, MIN_REGRESSION_DAYS)
        self.assertLessEqual((end - start).days, 62, "analyst_market_evaluation refuses a longer window")

    def test_weekly_review_fits_on_the_trailing_window_and_keeps_the_seven_day_points(self):
        windows = []

        def evaluation(_database, start, end, *_args, **_kwargs):
            windows.append((start, end))
            return {"timeline": []}

        def points(_database, start, end, _evaluation):
            return [{"exchange_date": str(start + timedelta(days=offset)), "net_direction_score": offset,
                     "positive_claims": offset, "negative_claims": 0, "concept_positive_ratio": offset,
                     "market_mean_change_pct": 2.0 * offset}
                    for offset in range((end - start).days + 1)]

        class Row:
            def fetchone(self):
                return {"review_id": 1, "generated_at": None}

        class Connection:
            def execute(self, *_args, **_kwargs):
                return Row()

        class Database:
            @contextmanager
            def transaction(self):
                yield Connection()

        with patch.object(analyst_market_review, "analyst_market_evaluation", evaluation), \
                patch.object(analyst_market_review, "_market_points", points):
            result = analyst_market_review.build_analyst_market_review(Database(), "weekly", date(2026, 10, 9))
        review_start, review_end = _period("weekly", date(2026, 10, 9))
        self.assertIn((review_start, review_end), windows, "the report's own period is still evaluated")
        self.assertIn(_regression_window(date(2026, 10, 9)), windows)
        self.assertEqual(result["status"], "ready")


if __name__ == "__main__":
    unittest.main()
