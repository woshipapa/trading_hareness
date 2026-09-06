from datetime import datetime, timezone
import asyncio
import unittest

from app.longhu_auction_capture import capture, capture_window, normalize


class LonghuAuctionCaptureTests(unittest.TestCase):
    def test_auction_window_uses_shanghai_session_clock(self):
        self.assertTrue(capture_window(datetime(2026, 9, 4, 1, 26, tzinfo=timezone.utc)))
        self.assertFalse(capture_window(datetime(2026, 9, 4, 0, 59, tzinfo=timezone.utc)))

    def test_normalize_preserves_raw_auction_row_with_a_strict_symbol(self):
        observed = datetime(2026, 9, 4, 1, 26, tzinfo=timezone.utc)
        events = normalize({"list": [["600000", "demo"], ["bad", "x"]]}, observed)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["ts_code"], "600000.SH")
        self.assertEqual(events[0]["event_type"], "longhu_morning_auction")
        self.assertEqual(events[0]["raw"]["live_effect"], "none")

    def test_capture_persists_only_during_auction_window(self):
        captured = []

        async def call(_request):
            return {"pages": [{"payload": {"list": [["000001", 1]]}}]}

        async def persist(provider, events):
            captured.extend(events)
            return len(events)

        result = asyncio.run(capture(datetime(2026, 9, 4, 1, 26, tzinfo=timezone.utc), call=call, persist=persist))
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["stored"], 1)
        self.assertEqual(captured[0]["ts_code"], "000001.SZ")
