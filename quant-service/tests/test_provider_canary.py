from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from app.provider_canary import classify_canary_failure, validate_quote_canary


class ProviderCanaryTests(unittest.TestCase):
    observed_at = datetime(2026, 9, 29, 8, 0, tzinfo=timezone.utc)

    def test_empty_payload_is_not_decision_eligible(self) -> None:
        result = validate_quote_canary([], observed_at=self.observed_at)

        self.assertEqual(result["status"], "empty")
        self.assertEqual(result["reasons"], ["no_rows"])
        self.assertFalse(result["decision_eligible"])

    def test_healthy_payload_requires_valid_shape_and_fresh_timestamp(self) -> None:
        result = validate_quote_canary(
            [
                {
                    "open": 10,
                    "high": 12,
                    "low": 9,
                    "close": 11,
                    "exchange_time": "2026-09-29T07:59:30+00:00",
                }
            ],
            observed_at=self.observed_at,
        )

        self.assertEqual(result["status"], "healthy")
        self.assertTrue(result["decision_eligible"])

    def test_malformed_prices_and_stale_timestamp_are_blocking(self) -> None:
        result = validate_quote_canary(
            [
                {
                    "open": 10,
                    "high": 8,
                    "low": 9,
                    "close": 11,
                    "exchange_time": (
                        self.observed_at - timedelta(seconds=91)
                    ).isoformat(),
                }
            ],
            observed_at=self.observed_at,
        )

        self.assertEqual(result["status"], "invalid")
        self.assertFalse(result["decision_eligible"])
        self.assertIn("row_0:ohlc_relationship", result["reasons"])
        self.assertIn("stale_timestamp", result["reasons"])

    def test_missing_and_future_timestamps_are_blocking(self) -> None:
        missing = validate_quote_canary(
            [{"open": 10, "high": 11, "low": 9, "close": 10}],
            observed_at=self.observed_at,
        )
        future = validate_quote_canary(
            [
                {
                    "open": 10,
                    "high": 11,
                    "low": 9,
                    "close": 10,
                    "timestamp": "2026-09-29T08:00:06+00:00",
                }
            ],
            observed_at=self.observed_at,
        )

        self.assertIn("row_0:missing_timestamp", missing["reasons"])
        self.assertIn("future_timestamp", future["reasons"])
        self.assertFalse(missing["decision_eligible"])
        self.assertFalse(future["decision_eligible"])

    def test_failure_classification_keeps_operational_causes_distinct(self) -> None:
        self.assertEqual(classify_canary_failure("request timed out"), "timeout")
        self.assertEqual(classify_canary_failure("permission denied"), "rejected")
        self.assertEqual(classify_canary_failure("no data returned"), "empty")
        self.assertEqual(classify_canary_failure("connection reset"), "unreachable")


if __name__ == "__main__":
    unittest.main()
