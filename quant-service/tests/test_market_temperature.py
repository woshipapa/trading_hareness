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
    def test_coverage_names_missing_limit_and_previous_sealed_components(self):
        rows = _ordinary(80)
        missing_limits = mt.temperature_series(rows + [_row(date(2099, 4, 1), limits_ok=False)])[-1]
        missing_previous = mt.temperature_series(rows + [_row(date(2099, 4, 1), prev_sealed=0, premium=None)])[-1]
        complete = mt.temperature_series(rows + [_row(date(2099, 4, 1))])[-1]
        self.assertEqual(missing_limits["coverage"]["missing"], ["limit_up", "limit_down", "seal_rate", "promotion", "max_streak"])
        self.assertEqual(missing_previous["coverage"]["missing"], ["premium", "promotion"])
        self.assertEqual(complete["coverage"]["missing"], [])
        self.assertNotEqual(missing_limits["coverage"], complete["coverage"])

    def test_fewer_than_five_scored_components_still_reports_coverage(self):
        rows = _ordinary(80)
        row = _row(date(2099, 4, 1), limits_ok=False, advancers=0, decliners=0, prev_sealed=0, turnover=None)
        reading = mt.temperature_series(rows + [row])[-1]
        self.assertIsNone(reading["temperature"])
        self.assertLess(reading["coverage"]["scored"], 5)
        self.assertEqual(reading["coverage"]["missing"], ["limit_up", "limit_down", "seal_rate", "up_ratio", "premium", "promotion", "max_streak", "turnover_ratio"])
    def test_record_high_and_low_are_scored_against_prior_history(self):
        rows = _ordinary(60)
        high = _row(date(2099, 3, 2), limit_up=150)
        low = _row(date(2099, 3, 3), limit_up=1)
        high_reading = mt.temperature_series(rows + [high])[-1]
        low_reading = mt.temperature_series(rows + [low])[-1]
        self.assertEqual(high_reading["scores"]["limit_up"], 100.0)
        self.assertEqual(low_reading["scores"]["limit_up"], 0.0)

    def test_a_value_equal_to_every_prior_value_scores_fifty(self):
        rows = [_row(date(2099, 1, 1) + timedelta(days=i), limit_up=50) for i in range(60)]
        reading = mt.temperature_series(rows + [_row(date(2099, 3, 3), limit_up=50)])[-1]
        self.assertEqual(reading["scores"]["limit_up"], 50.0)

    def test_the_day_is_not_in_its_own_history(self):
        rows = _ordinary(60)
        last = _row(date(2099, 3, 2), limit_up=150)
        reading = mt.temperature_series(rows + [last])[-1]
        self.assertEqual(reading["scores"]["limit_up"], 100.0)

    def test_the_last_reading_matches_the_prior_history_scoring_rule(self):
        rows = _ordinary(80)
        prepared = mt.with_turnover_ratio(rows)
        values = mt.components_of(prepared[-1])
        expected_scores, expected_temperature = mt.score(values, mt.history_for(rows[:-1]))
        reading = mt.temperature_series(rows)[-1]
        self.assertEqual(reading["scores"], expected_scores)
        self.assertEqual(reading["temperature"], expected_temperature)

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


class FrozenRulesTests(unittest.TestCase):
    #: Written on 2026-10-14.  If this fails you changed something that decides a band (a component, its
    #: direction, the window, a gate or a band edge).  Raise ``VERSION``, set a new ``FROZEN_ON`` and
    #: ``FORWARD_FROM``, record the change in decision 0013, and only then replace this literal.
    FROZEN_SHA256 = "922ef3cdc2956804a10600359f04b1f3cbf55a6dc03785593b35af9330ce4471"

    def test_the_band_rules_are_the_frozen_ones(self):
        self.assertEqual(mt.RULES_SHA256, self.FROZEN_SHA256)

    def test_the_band_edges_in_the_fingerprint_are_the_ones_band_of_uses(self):
        for temperature, band in ((20.0, "冰点"), (20.1, "冷"), (39.9, "冷"), (40.0, "中性"), (59.9, "中性"),
                                  (60.0, "热"), (79.9, "热"), (80.0, "沸点")):
            self.assertEqual(mt.band_of(temperature), band)
        self.assertEqual(mt.RULES["band_edges"], [mt.FREEZING, mt.COLD_EDGE, mt.WARM_EDGE, mt.BOILING])

    def _day(self, index: int, band: str, *, opening: float = 100.0, closing: float = 100.0, sha=None, start=date(2026, 10, 15)):
        return {"trade_date": (start + timedelta(days=index)).isoformat(), "band": band, "index_open": opening,
                "index_close": closing, "rules_sha256": sha or mt.RULES_SHA256}

    def test_the_forward_sample_collects_until_both_groups_have_twenty_days(self):
        readings = [self._day(index, "冰点" if index % 2 == 0 else "中性", closing=100.0 + index % 3)
                    for index in range(30)]
        result = mt.forward_test(readings)
        self.assertEqual(result["status"], "collecting")
        self.assertLess(result["freezing_days"], mt.FORWARD_MIN_DAYS)

    def test_the_forward_sample_judges_the_written_direction_once_it_has_twenty_each(self):
        # the session after a freezing day closes 1% above its open, after any other day 0.5% below
        readings = []
        for index in range(100):
            band = "冰点" if index % 2 == 0 else "中性"
            previous_band = "冰点" if (index - 1) % 2 == 0 else "中性"
            readings.append(self._day(index, band, closing=101.0 + (index % 3) * 0.01 if previous_band == "冰点"
                                      else 99.5 + (index % 3) * 0.01))
        result = mt.forward_test(readings)
        self.assertEqual(result["status"], "supports")
        self.assertGreater(result["difference"], 0.0)
        self.assertGreaterEqual(result["t"], 2)
        reversed_readings = [{**reading, "index_close": 200.0 - reading["index_close"]} for reading in readings]
        self.assertEqual(mt.forward_test(reversed_readings)["status"], "against")

    def test_readings_before_the_freeze_or_under_other_rules_are_not_counted(self):
        before = [self._day(index, "冰点", closing=102.0, start=date(2026, 9, 1)) for index in range(40)]
        other_rules = [self._day(index, "冰点", closing=102.0, sha="0" * 64) for index in range(40)]
        result = mt.forward_test(before + other_rules)
        self.assertEqual((result["freezing_days"], result["other_days"], result["bands"]), (0, 0, {}))

    def test_a_missing_open_or_a_long_gap_leaves_the_pair_out(self):
        readings = [self._day(0, "冰点"), {**self._day(1, "中性"), "index_open": None}, self._day(30, "中性")]
        self.assertEqual(mt.forward_test(readings)["bands"], {})


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
