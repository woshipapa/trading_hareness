import unittest
import asyncio
from datetime import datetime, timezone

from app.market_event_capture import capture, normalize_fuyao_auction, normalize_fuyao_events


class MarketEventCaptureTests(unittest.TestCase):
    observed_at = datetime(2026, 8, 31, 7, 0, tzinfo=timezone.utc)

    def test_pool_rows_keep_distinct_observation_minute_identity(self):
        rows = normalize_fuyao_events("a_share_limit_break_pool", {
            "item": [{"thscode": "300001.SZ", "name": "测试", "open_times": 2}],
        }, self.observed_at)
        self.assertEqual(rows[0]["event_type"], "limit_open_pool")
        self.assertIn("202608310700", rows[0]["event_identity_key"])
        self.assertEqual(rows[0]["raw"]["open_times"], 2)

    def test_auction_rows_are_final_evidence(self):
        rows = normalize_fuyao_auction({
            "auction_phase": "closed", "data_status": "final",
            "item": [{"thscode": "000001.SZ", "name": "平安银行", "auction_price": 11.2}],
        }, self.observed_at)
        self.assertEqual(rows[0]["event_type"], "auction_final")
        self.assertEqual(rows[0]["raw"]["data_status"], "final")

    def test_capture_reports_each_capability_health_independently(self):
        health = []

        async def fetch(capability, _params):
            if capability == "a_share_limit_up_pool":
                return {"item": [{"thscode": "000001.SZ"}]}
            return {"item": []}

        async def persist(_provider, rows):
            return len(rows)

        async def persist_health(provider, capability, rows, error):
            health.append((provider, capability, rows, error))

        result = asyncio.run(capture(
            self.observed_at, fetch=fetch, persist=persist, persist_health=persist_health,
        ))
        self.assertEqual(result["status"], "completed")
        self.assertEqual(len(health), 4)
        self.assertEqual(health[0], ("fuyao_ths", "a_share_limit_up_pool", 1, None))


if __name__ == "__main__":
    unittest.main()
