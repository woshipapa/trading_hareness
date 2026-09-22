"""The per-scan tape covers every stock; heavy evidence is sampled unless a signal fired."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from app.watch_scan_tape import EvidenceThrottle, tape_record


def at(seconds: float) -> datetime:
    return datetime(2026, 9, 22, 1, 40, tzinfo=timezone.utc) + timedelta(seconds=seconds)


class EvidenceThrottleTests(unittest.TestCase):
    def test_heavy_evidence_is_sampled_but_always_kept_on_a_signal(self):
        throttle = EvidenceThrottle(30)
        kept = [throttle.due("A", at(s)) for s in (0, 5, 10, 25, 30, 35)]
        self.assertEqual(kept, [True, False, False, False, True, False])
        self.assertTrue(throttle.due("A", at(40), force=True))
        self.assertTrue(throttle.due("B", at(41)))                    # per stock
        self.assertTrue(throttle.due("A", at(40) + timedelta(days=1)))  # new session


class TapeRecordTests(unittest.TestCase):
    def test_record_holds_the_measurements_a_review_needs(self):
        quote = {"price": 26.47, "pct_change": 10.0, "volume_ratio": 0.3, "turnover_rate": 0.6,
                 "price_source": "longhuvip_watch_quote", "price_freshness": {"status": "fresh"},
                 "raw": {"longhu_watch_quote": {"pre_close": 24.06, "open": 26.47, "high": 26.47, "low": 26.47,
                                                "amount": 3.98e7, "volume": 15054,
                                                "order_book": {"book_side": "bid_only", "bids": [{"price": 26.47, "size": 1}],
                                                               "asks": [], "seal_volume_lot": 1}}}}
        minute = {"price": 26.47, "vwap": 26.47, "return_1m_pct": 0.0, "return_5m_pct": 0.0,
                  "minute_volume_multiple": 0.4, "above_vwap_pct": 0.0, "time": "1130"}
        peer = {"selected_group": {"group_key": "ths:881101", "label": "家居用品"}, "group_breadth": 0.6,
                "confirming_breadth": 0.4, "confirming_peer_count": 2, "available_peer_count": 5}
        signals = [{"signal_type": "entry", "state": "confirmed", "signal_key": "001216.SZ:entry:teacher_review:relay_one_word"}]
        record = tape_record("001216.SZ", quote, minute, peer, signals, at(0))
        self.assertEqual((record["p"], record["o"], record["pc"], record["v"], record["a"]), (26.47, 26.47, 24.06, 15054.0, 3.98e7))
        self.assertTrue(record["sealed"])
        self.assertEqual(record["m"]["t"], "1130")
        self.assertEqual(record["sec"]["g"], "ths:881101")
        self.assertEqual(record["sig"], ["entry:confirmed:001216.SZ:entry:teacher_review:relay_one_word"])
        self.assertLess(len(str(record)), 800)

    def test_a_stock_without_a_quote_still_gets_its_minute_and_sector_view(self):
        record = tape_record("000001.SZ", None, {"price": 10.0, "vwap": 10.1}, {}, [], at(0))
        self.assertNotIn("p", record)
        self.assertIn("m", record)


class TapePersistenceTests(unittest.TestCase):
    def test_non_json_values_cannot_break_the_scan_transaction(self):
        from decimal import Decimal
        from app.watch_scan_tape import persist_scan_tape
        executed = []

        class Connection:
            def execute(self, sql, params):
                import json as _json
                _json.dumps(params[5].obj)          # what psycopg's Json adapter will do
                executed.append(params)
                return self

            def fetchone(self):
                return {"observation_id": 1}

        self.assertTrue(persist_scan_tape(Connection(), scan_id="s", observed_at=at(0),
                                          rows={"A": {"p": 1.0, "sec": {"cp": Decimal("2")}}}))
        self.assertEqual(executed[0][5].obj["rows"]["A"]["sec"]["cp"], "2")


if __name__ == "__main__":
    unittest.main()
