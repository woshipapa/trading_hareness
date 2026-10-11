"""Health of the decision-0013 indicators: stored, complete, and from the primary source."""

from __future__ import annotations

import unittest
from datetime import date, datetime
from unittest import mock

from app import indicator_health as health
from app.derived_daily_readings import CN_TZ

DAY = date(2099, 3, 4)
AFTER = datetime(2099, 3, 4, 18, 0, tzinfo=CN_TZ)


def _status(key: str, reading: dict | None) -> dict:
    with mock.patch.object(health.derived_daily_readings, "newest", return_value=[reading] if reading else []):
        return health.indicator_status(object(), key, DAY, AFTER)


class TemperatureHealthTests(unittest.TestCase):
    def test_a_full_reading_is_eligible_and_a_thin_one_warns(self):
        full = {"temperature": 72.6, "scores": dict.fromkeys("abcdefgh", 50.0)}
        self.assertTrue(_status("market.temperature", full)["decision_eligible"])
        thin = {"temperature": 40.0, "scores": {**dict.fromkeys("abcdef", 50.0), "g": None, "h": None}}
        self.assertEqual(_status("market.temperature", thin)["status"], "warn")
        self.assertEqual(_status("market.temperature", None)["status"], "missing")

    def test_etf_flow_needs_the_whole_basket_and_exact_turnover(self):
        self.assertEqual(_status("market.broad_etf_flow", {"ratio": 1.54, "codes": 12})["status"], "ok")
        self.assertEqual(_status("market.broad_etf_flow", {"ratio": 1.54, "codes": 10})["status"], "warn")
        self.assertEqual(_status("market.broad_etf_flow", {"ratio": 1.54, "codes": 12, "amount_estimated": True})["status"], "warn")
        self.assertEqual(_status("market.broad_etf_flow", {"ratio": None, "codes": 5})["status"], "missing")

    def test_timing_from_the_fallback_source_warns(self):
        self.assertEqual(_status("market.timing", {"state": "silver", "source": "fuyao_ths"})["status"], "ok")
        self.assertEqual(_status("market.timing", {"state": "silver", "source": "tencent_free"})["status"], "warn")

    def test_post_close_indicators_wait_for_the_close(self):
        during = datetime(2099, 3, 4, 10, 31, tzinfo=CN_TZ)
        for key in ("market.temperature", "market.broad_etf_flow", "market.timing"):
            self.assertEqual(health.indicator_status(object(), key, DAY, during)["status"], "pending", key)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
