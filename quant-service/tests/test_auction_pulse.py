from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app.auction_pulse import alert_decision, auction_pulse_window, format_alert, summarize_pulse


class AuctionPulseTests(unittest.TestCase):
    def test_window_is_shanghai_opening_auction_only(self):
        tz = ZoneInfo("Asia/Shanghai")
        self.assertTrue(auction_pulse_window(datetime(2026, 9, 21, 9, 15, tzinfo=tz)))
        self.assertTrue(auction_pulse_window(datetime(2026, 9, 21, 9, 30, tzinfo=tz)))
        self.assertFalse(auction_pulse_window(datetime(2026, 9, 21, 9, 31, tzinfo=tz)))
        self.assertFalse(auction_pulse_window(datetime(2026, 9, 20, 9, 20, tzinfo=tz)))

    def test_summarizes_breadth_money_and_sector_evidence_without_inventing_missing_values(self):
        summary = summarize_pulse({
            "breadth": {"action": "RiseFallAnalysis", "payload": {"list": [{"上涨家数": 120, "下跌家数": 80}]}},
            "mood": {"action": "MoodNumCount", "payload": {"涨停家数": 8, "跌停家数": 2}},
            "sector": {"action": "GetBKJJ_W36", "payload": {"list": [
                {"板块名称": "半导体", "涨跌幅": 2.1, "净流入": 123.0},
                {"板块名称": "银行", "涨跌幅": -1.2, "净流入": -80.0},
            ]}},
        }, datetime(2026, 9, 21, 1, 20, tzinfo=timezone.utc))
        self.assertEqual(summary["sentiment"]["breadth_ratio"], 0.2)
        self.assertEqual(summary["money_flow"]["status"], "observed")
        self.assertEqual(summary["sector_anomalies"][0]["label"], "半导体")
        self.assertTrue(summary["research_only"])
        self.assertEqual(summary["live_effect"], "none")

    def test_summarizes_gateway_page_envelopes(self):
        summary = summarize_pulse({
            "sector": {"action": "GetBKJJ_W36", "payload": {"pages": [{"payload": {"list": [
                {"板块名称": "机器人", "涨跌幅": 3.0, "净流入": 99.0},
            ]}}]}},
        }, datetime(2026, 9, 21, 1, 20, tzinfo=timezone.utc))
        self.assertEqual(summary["sector_anomalies"][0]["label"], "机器人")

    def test_alerts_state_changes_with_cooldown(self):
        now = datetime.now(timezone.utc)
        summary = summarize_pulse({"breadth": {"payload": {"up": 2, "down": 1}}}, now)
        first = alert_decision(summary, None, now=now, last_alert_at=None)
        self.assertTrue(first["should_send"])
        same = alert_decision(summary, summary, now=now, last_alert_at=now)
        self.assertFalse(same["should_send"])
        changed = summarize_pulse({"breadth": {"payload": {"up": 1, "down": 2}}}, now)
        cooled = alert_decision(changed, summary, now=now + timedelta(seconds=31), last_alert_at=now)
        self.assertTrue(cooled["should_send"])
        self.assertIn("研究观察", format_alert(changed))


if __name__ == "__main__":
    unittest.main()
