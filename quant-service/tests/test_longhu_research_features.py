from datetime import date, datetime, timezone
import unittest

from app.longhu_research_features import next_session_context, normalize_payload, payload_rows, strict_symbol


class LonghuResearchFeatureTests(unittest.TestCase):
    def test_strict_symbol_requires_exchange_or_known_code_prefix(self):
        self.assertEqual(strict_symbol("600000.SH"), "600000.SH")
        self.assertEqual(strict_symbol("SZ000001"), "000001.SZ")
        self.assertIsNone(strict_symbol("bad"))

    def test_payload_rows_preserves_vendor_array_shape_and_normalizes_provenance(self):
        payload = {"errcode": 0, "list": [["600000", 1], ["000001.SZ", 2]]}
        self.assertEqual(payload_rows(payload), payload["list"])
        rows = normalize_payload(
            target="longhu_market_wide", action="GetPlateInfo_w38", controller="DailyLimitResumption",
            payload=payload, trade_date=date(2026, 9, 4),
            observed_at=datetime(2026, 9, 4, 8, tzinfo=timezone.utc),
        )
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["target"], "longhu_market_wide")
        self.assertEqual(rows[0]["ts_code"], "600000.SH")
        self.assertEqual(rows[0]["exchange_date"], "2026-09-04")
        self.assertTrue(rows[0]["research_only"])
        self.assertEqual(rows[0]["live_effect"], "none")
        self.assertEqual(rows[0]["payload"], ["600000", 1])

    def test_history_payload_carries_next_session_boundary(self):
        rows = normalize_payload(
            target="longhu_history", action="MorningBiddingList", controller="HisHomeDingPan",
            payload={"errcode": 0}, trade_date=date(2026, 9, 4),
            observed_at=datetime(2026, 9, 4, 8, tzinfo=timezone.utc),
            availability_basis="post_close_history_replay", next_session_only=True,
        )
        self.assertTrue(rows[0]["next_session_only"])
        self.assertEqual(rows[0]["availability_basis"], "post_close_history_replay")

    def test_next_session_context_rejects_same_day_payloads(self):
        result = next_session_context([
            {"capability": "longhu:MorningBiddingList", "symbol": "600000.SH", "available_at": "x", "payload": {"exchange_date": "2026-09-03"}},
            {"capability": "longhu:GetList", "symbol": None, "available_at": "y", "payload": {"exchange_date": "2026-09-04"}},
        ], date(2026, 9, 4))
        self.assertEqual(result["status"], "available")
        self.assertEqual(result["capabilities"]["longhu:MorningBiddingList"]["symbols"], ["600000.SH"])
        self.assertEqual(result["rejected_same_day_rows"], 1)


class SourceDateTests(unittest.TestCase):
    def test_the_vendor_s_own_date_is_kept_beside_the_capture_session(self):
        kwargs = dict(target="longhu_market_wide", action="GetPlateInfo_w38", controller="DailyLimitResumption",
                      trade_date=date(2026, 10, 9), observed_at=datetime(2026, 10, 9, 8, tzinfo=timezone.utc))
        dated = normalize_payload(payload={"date": "2026-10-08", "list": [{"ZSCode": "801004"}]}, **kwargs)
        self.assertEqual((dated[0]["exchange_date"], dated[0]["source_date"]), ("2026-10-09", "2026-10-08"))
        self.assertIsNone(normalize_payload(payload={"list": [{"ZSCode": "801004"}]}, **kwargs)[0]["source_date"])
