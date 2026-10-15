from __future__ import annotations

import unittest

from app.intraday_scan_timing import build_scan_performance


class IntradayScanTimingTests(unittest.TestCase):
    def test_projection_is_stable_research_only_and_ignores_unknown_stages(self) -> None:
        value = build_scan_performance(
            status="completed",
            stage_ms={"signal_persist": 12.345, "capture_and_memberships": -2, "internal": 99},
            total_ms="18.76",
        )
        self.assertEqual(value["schema_version"], "intraday-scan-timing-v1")
        self.assertTrue(value["research_only"])
        self.assertEqual(value["live_effect"], "none")
        self.assertEqual(value["total_ms"], 18.8)
        self.assertEqual(value["stage_ms"], {"capture_and_memberships": 0.0, "signal_persist": 12.3})
        self.assertEqual(value["overlap"]["capture_and_memberships"], "concurrent")
        self.assertEqual(value["overlap"]["shadow_rotation_and_delivery"], "concurrent_after_receipt")


if __name__ == "__main__":
    unittest.main()
