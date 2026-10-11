"""Valuation projection semantics, independently of provider transport."""
import unittest
from datetime import date, datetime, timezone
from decimal import Decimal

from app.datasources.derived.daily_valuations import projection_row

DAY = date(2026, 10, 9)
CAPTURE = datetime(2026, 10, 9, 10, 30, tzinfo=timezone.utc)


class ValuationProjectionTests(unittest.TestCase):
    def evidence(self, **updates):
        return {"symbol": "920001.BJ", "effective_at": CAPTURE, "available_at": CAPTURE,
                "payload_sha256": "fixture-hash", "normalized": {
                    "ts_code": "920001.BJ", "pe_ttm": -41.04, "pb_mrq": 2.45,
                    "pe_mrq": -67.0, "ps_ttm": 7.6, "pcf_ttm": -102.6}, **updates}

    def test_negative_pe_is_measurement_and_basis_is_explicit(self):
        row = projection_row(self.evidence(), DAY, CAPTURE)
        self.assertEqual(row["pe"], Decimal("-41.04"))
        self.assertEqual(row["pb"], Decimal("2.45"))
        self.assertEqual(row["raw"]["field_basis"], {"pe": "pe_ttm", "pb": "pb_mrq"})
        self.assertEqual(row["raw"]["source_values"]["pe_mrq"], -67.0)
        self.assertNotIn("turnover_rate", row)

    def test_backfill_never_moves_availability_backwards(self):
        weekend = datetime(2026, 10, 10, 2, tzinfo=timezone.utc)
        row = projection_row(self.evidence(), DAY, weekend)
        self.assertEqual(row["available_at"], weekend)
        self.assertEqual(row["raw"]["source_available_at"], CAPTURE.isoformat())

    def test_stale_future_intraday_and_mismatched_symbols_are_rejected(self):
        for updates in (
            {"effective_at": datetime(2026, 10, 8, 10, tzinfo=timezone.utc)},
            {"effective_at": datetime(2026, 10, 9, 3, tzinfo=timezone.utc)},
            {"available_at": datetime(2026, 10, 10, 2, tzinfo=timezone.utc)},
            {"symbol": "000300.SH"},
            {"normalized": {"ts_code": "920002.BJ", "pe_ttm": 2}},
            {"effective_at": CAPTURE.replace(tzinfo=None)},
            {"normalized": {"ts_code": "920001.BJ", "trade_date": "2026-10-08", "pe_ttm": 2}},
        ):
            with self.subTest(updates=updates):
                self.assertIsNone(projection_row(self.evidence(**updates), DAY, CAPTURE))

    def test_empty_nonfinite_and_boolean_ratios_are_not_measurements(self):
        for values in ({}, {"pe_ttm": "nan", "pb_mrq": "Infinity"}, {"pe_ttm": True}):
            row = self.evidence(normalized={"ts_code": "920001.BJ", **values})
            self.assertIsNone(projection_row(row, DAY, CAPTURE))

    def test_partial_fields_remain_null_and_zero_is_valid(self):
        row = projection_row(self.evidence(normalized={"ts_code": "920001.BJ", "pb_mrq": 0}), DAY, CAPTURE)
        self.assertIsNone(row["pe"])
        self.assertEqual(row["pb"], Decimal(0))

    def test_automatic_wiring_is_off_until_a_writer_is_selected(self):
        from app.datasources.runtime import build_archive_deps
        disabled = build_archive_deps(object(), object(), environ={})
        enabled = build_archive_deps(object(), object(), environ={"DAILY_VALUATION_PROJECTION_ENABLED": "true"})
        self.assertIsNone(disabled.project_valuations)
        self.assertTrue(callable(enabled.project_valuations))


if __name__ == "__main__":
    unittest.main()
