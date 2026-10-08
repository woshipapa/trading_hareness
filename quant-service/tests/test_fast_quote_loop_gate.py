from __future__ import annotations

import unittest

from app.intraday_fast_quote_runtime import fast_quote_loop_enabled

CONFIGURED = {
    "TUSHARE_SUPER_GET_MODE": "promax",
    "TUSHARE_SUPER_GET_API_URL": "https://gateway.example/tushare/pro",
    "TUSHARE_SUPER_GET_API_KEY": "fixture-key-not-real",
}


class FastQuoteLoopGateTests(unittest.TestCase):
    def test_runs_when_the_super_get_route_is_configured(self):
        self.assertTrue(fast_quote_loop_enabled(30, environ=CONFIGURED))

    def test_does_not_start_without_a_super_get_route(self):
        # Every tick would otherwise record a failure and hold realtime_quote degraded.
        self.assertFalse(fast_quote_loop_enabled(30, environ={}))
        self.assertFalse(fast_quote_loop_enabled(30, environ={"TUSHARE_SUPER_GET_MODE": "promax"}))

    def test_a_key_without_an_endpoint_is_not_a_route(self):
        self.assertFalse(fast_quote_loop_enabled(30, environ={
            "TUSHARE_SUPER_GET_MODE": "promax", "TUSHARE_SUPER_GET_API_KEY": "fixture-key-not-real",
        }))

    def test_short_scan_interval_still_disables_it(self):
        self.assertFalse(fast_quote_loop_enabled(20, environ=CONFIGURED))


if __name__ == "__main__":
    unittest.main()
