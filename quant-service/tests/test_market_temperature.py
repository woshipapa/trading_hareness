"""The sentiment temperature: bands, percentile scores, missing inputs, turnover ratio, and point-in-time readings."""

from __future__ import annotations

import unittest
from datetime import date, timedelta

from app import market_temperature as mt


def _row(day: date, *, limit_up=50, touched=70, limit_down=10, advancers=2500, decliners=2500, prev_sealed=50,
         promoted=10, premium=1.0, max_streak=4, turnover=1.5e12, limits_ok=True) -> dict:
    return {"trading_date": day, "limit_up": limit_up, "touched": touched, "limit_down": limit_down,
            "advancers": advancers, "decliners": decliners, "prev_sealed": prev_sealed, "promoted": promoted,
            "premium_pct": premium, "max_streak": max_streak, "turnover_cny": turnover, "limits_ok": limits_ok,
            "index_close": 3900.0}


def _ordinary(days: int, start: date = date(2099, 1, 1)) -> list[dict]:
    """A year of unremarkable sessions whose values wobble around their middle."""
    rows = []
    for i in range(days):
        wobble = (i * 7) % 11 - 5          # -5..5, no trend
        rows.append(_row(start + timedelta(days=i), limit_up=50 + wobble, touched=72 + wobble, limit_down=10 - wobble // 2,
                         advancers=2500 + 40 * wobble, decliners=2500 - 40 * wobble, promoted=10 + wobble // 2,
                         premium=1.0 + wobble / 10, max_streak=4 + (wobble > 3), turnover=1.5e12 * (1 + wobble / 100)))
    return rows


class BandTests(unittest.TestCase):
    def test_the_edges_follow_the_freezing_and_boiling_points(self):
        cases = {None: None, 0: "冰点", 20: "冰点", 20.1: "冷", 39.9: "冷", 40: "中性", 59.9: "中性",
                 60: "热", 79.9: "热", 80: "沸点", 100: "沸点"}
        for value, band in cases.items():
            self.assertEqual(mt.band_of(value), band, value)


class ScoreTests(unittest.TestCase):
    def test_percentile_rank_counts_ties_half(self):
        self.assertEqual(mt.percentile_rank(2.5, [1, 2, 3, 4]), 50.0)
        self.assertEqual(mt.percentile_rank(2, [1, 2, 3, 4]), 37.5)

    def test_missing_limit_prices_leave_limit_components_unknown_not_zero(self):
        values = mt.components_of(_row(date(2099, 1, 1), limits_ok=False))
        for key in ("limit_up", "limit_down", "seal_rate", "promotion", "max_streak"):
            self.assertIsNone(values[key], key)
        self.assertEqual(values["up_ratio"], 0.5)
        self.assertEqual(values["premium"], 1.0)

    def test_no_previous_limit_ups_means_no_premium_or_promotion(self):
        values = mt.components_of(_row(date(2099, 1, 1), prev_sealed=0, premium=None))
        self.assertIsNone(values["premium"])
        self.assertIsNone(values["promotion"])

    def test_turnover_ratio_needs_twenty_earlier_sessions(self):
        rows = mt.with_turnover_ratio(_row(date(2099, 1, 1) + timedelta(days=i), turnover=1e12) for i in range(21))
        self.assertIsNone(rows[19]["turnover_ratio"])
        self.assertEqual(rows[20]["turnover_ratio"], 1.0)


class SeriesTests(unittest.TestCase):
    def test_extreme_days_read_as_boiling_and_freezing(self):
        rows = _ordinary(120)
        hot = _row(date(2099, 6, 1), limit_up=150, touched=160, limit_down=0, advancers=4800, decliners=200,
                   promoted=40, premium=6.0, max_streak=9, turnover=2.4e12)
        cold = _row(date(2099, 6, 2), limit_up=5, touched=40, limit_down=90, advancers=300, decliners=4700,
                    promoted=0, premium=-6.0, max_streak=1, turnover=0.9e12)
        series = mt.temperature_series(rows + [hot, cold])
        self.assertEqual(series[-2]["band"], "沸点")
        self.assertEqual(series[-1]["band"], "冰点")
        self.assertIsNone(series[10]["temperature"], "no score before MIN_HISTORY sessions")

    def test_a_reading_never_changes_when_later_sessions_arrive(self):
        rows = _ordinary(150)
        early = mt.temperature_series(rows[:100])
        later = mt.temperature_series(rows)
        self.assertEqual(early, later[:100])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
