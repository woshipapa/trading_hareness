from __future__ import annotations

from datetime import datetime, timedelta, timezone
import unittest

from app.market_event_runtime import event_capture_window
from app.market_rules import china_equity_observation_session, china_equity_session


CN = timezone(timedelta(hours=8))


class MarketObservationClockTests(unittest.TestCase):
    def test_call_auction_opens_evidence_only(self) -> None:
        observed_at = datetime(2026, 9, 21, 9, 15, tzinfo=CN)
        self.assertTrue(china_equity_observation_session(observed_at)[0])
        self.assertFalse(china_equity_session(observed_at)[0])
        self.assertEqual(event_capture_window(observed_at), (True, False))

    def test_preauction_and_weekend_remain_closed(self) -> None:
        self.assertFalse(china_equity_observation_session(datetime(2026, 9, 21, 9, 14, tzinfo=CN))[0])
        self.assertFalse(china_equity_observation_session(datetime(2026, 9, 20, 9, 15, tzinfo=CN))[0])


if __name__ == "__main__":
    unittest.main()
