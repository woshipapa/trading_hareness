from __future__ import annotations

import unittest
from datetime import date

from app.regime_stratification import (
    MIN_STRATUM_SESSIONS, STRATIFIED_SESSION_RETURNS_SQL, stratification_payload, stratify_session_returns,
)


def _rows(variant, returns, regime, stage):
    return [{"variant_key": variant, "period_date": date(2026, 1, 1), "period_return": value,
             "regime": regime, "sentiment_stage": stage} for value in returns]


class RegimeStratificationTests(unittest.TestCase):
    def test_a_regime_only_edge_shows_up_as_a_stratum_difference(self) -> None:
        rows = (_rows("post_close", [0.02, 0.03, 0.01], "trend_up", "fermenting")
                + _rows("post_close", [-0.01, -0.02, -0.03], "range_bound", "ebbing"))
        [line] = stratify_session_returns(rows)
        self.assertEqual(line["strategy_key"], "post_close")
        self.assertEqual(line["all"]["sessions"], 6)
        self.assertAlmostEqual(line["all"]["mean_net_return"], 0.0)
        by_key = {(item["kind"], item["stratum"]): item for item in line["strata"]}
        self.assertAlmostEqual(by_key[("regime", "trend_up")]["mean_net_return"], 0.02)
        self.assertAlmostEqual(by_key[("regime", "trend_up")]["mean_minus_all"], 0.02)
        self.assertAlmostEqual(by_key[("sentiment_stage", "ebbing")]["mean_net_return"], -0.02)
        self.assertEqual(by_key[("regime", "trend_up")]["hit_rate"], 1.0)

    def test_small_strata_are_reported_but_flagged(self) -> None:
        few = stratify_session_returns(_rows("s", [0.01, 0.02], "trend_up", "mixed"))[0]
        self.assertFalse(few["all"]["sample_sufficient"])
        enough = stratify_session_returns(_rows("s", [0.01, -0.005] * MIN_STRATUM_SESSIONS, "trend_up", "mixed"))[0]
        self.assertTrue(enough["all"]["sample_sufficient"])
        self.assertIsNotNone(enough["all"]["t_stat"])

    def test_one_session_has_no_dispersion_and_missing_readings_are_their_own_stratum(self) -> None:
        [line] = stratify_session_returns([{"variant_key": "s", "period_return": 0.01, "regime": None, "sentiment_stage": None}])
        self.assertIsNone(line["all"]["stdev"])
        self.assertIsNone(line["all"]["t_stat"])
        self.assertEqual({item["stratum"] for item in line["strata"]}, {"unknown"})

    def test_the_payload_is_research_only_and_the_query_is_point_in_time(self) -> None:
        payload = stratification_payload([], date(2026, 9, 25))
        self.assertEqual(payload["live_effect"], "none")
        self.assertTrue(payload["research_only"])
        self.assertEqual(payload["strategies"], [])
        # Readings of the signal date itself: computed from that close, known
        # when the idea was chosen, never the entry or exit session's.
        self.assertIn("regime.trading_date=o.as_of_date", STRATIFIED_SESSION_RETURNS_SQL)
        self.assertIn("sentiment.trading_date=o.as_of_date", STRATIFIED_SESSION_RETURNS_SQL)


if __name__ == "__main__":
    unittest.main()
