"""Small causal-oracle regressions for research-only time-series rules."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import math
import unittest

from app.paper_execution import triple_barrier_label
from app.strategy_contracts import LabelSpec
from app.technical_analysis import technical_summary


def assert_prefix_unchanged(test: unittest.TestCase, before, after, *, fields: tuple[str, ...]) -> None:
    """Compare the observable prefix while allowing an explicit missing result."""
    for field in fields:
        left, right = before.get(field), after.get(field)
        if isinstance(left, float) and math.isnan(left) and isinstance(right, float) and math.isnan(right):
            continue
        test.assertEqual(left, right, field)


class CausalityOracleTests(unittest.TestCase):
    def test_technical_summary_prefix_does_not_depend_on_future_bar_values(self) -> None:
        rows = [{"trade_date": f"202609{day:02d}", "close": 10 + day / 10} for day in range(1, 22)]
        baseline = technical_summary(rows, as_of_date="20260915")
        perturbed = technical_summary([
            *rows[:15],
            *[{"trade_date": row["trade_date"], "close": 10_000 if index % 2 else -10_000}
              for index, row in enumerate(rows[15:], start=15)],
        ], as_of_date="20260915")
        # A caller's explicit PIT boundary makes future perturbations
        # irrelevant to the current feature vector.
        assert_prefix_unchanged(
            self, baseline, perturbed,
            fields=("status", "score", "trend", "return_1d_pct", "return_5d_pct", "sma_5", "sma_10"),
        )
        self.assertEqual(baseline["status"], "ready")

    def test_missing_bar_is_a_quality_blocker_instead_of_a_silent_filter(self) -> None:
        result = technical_summary([
            {"trade_date": "20260901", "close": 10},
            {"trade_date": "20260902", "close": None},
            {"trade_date": "20260903", "close": 10.3},
        ])
        self.assertEqual(result["status"], "data_quality_blocked")
        self.assertEqual(result["score"], None)

    def test_triple_barrier_ignores_rows_after_the_declared_horizon(self) -> None:
        start = datetime(2026, 9, 29, 1, 30, tzinfo=timezone.utc)
        spec = LabelSpec(upper_return=0.03, lower_return=-0.02, max_horizon_minutes=30)
        prefix = [{"observed_at": start + timedelta(minutes=10), "close": 100.5}]
        with_future = [*prefix, {"observed_at": start + timedelta(minutes=90), "close": 10}]
        self.assertEqual(
            triple_barrier_label(prefix, entry_price=100, entry_at=start, spec=spec),
            triple_barrier_label(with_future, entry_price=100, entry_at=start, spec=spec),
        )


if __name__ == "__main__":
    unittest.main()
