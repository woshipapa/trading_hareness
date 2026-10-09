from __future__ import annotations

import unittest
from datetime import date

from app.longhu_market_sync import build_control_rows, merge_cross_section


class LonghuMarketSyncTests(unittest.TestCase):
    def test_merge_requires_same_date_tencent_ohlc_and_preserves_flow(self):
        vendor = {
            "600664.SH": {
                "symbol": "600664.SH", "name": "哈药股份", "close": 9.49,
                "main_net": 83_000_000, "turnover_rate": 8.65, "volume_ratio": 1.22,
                "pe": 18.6, "pb": 2.4, "total_mv": 25_000_000_000,
                "circ_mv": 20_000_000_000, "raw": {"vendor": True},
            }
        }
        quotes = [{
            "ts_code": "600664.SH", "name": "哈药股份", "trade_date": "20260901",
            "open": 9.3, "high": 9.58, "low": 9.18, "close": 9.49,
            "pre_close": 9.29, "vol": 123456, "amount": 1_250_005_000,
        }]
        result = merge_cross_section(date(2026, 9, 1), vendor, quotes)
        self.assertEqual(result.coverage, 1.0)
        self.assertEqual(result.daily_rows[0]["close"], 9.49)
        self.assertEqual(result.flow_rows[0]["net_amount"], 83_000_000)
        self.assertEqual(result.quote_rows[0]["provider_basis"], "longhuvip_licensed_dated_ohlc")

    def test_control_rows_use_transparent_identity_factor_and_board_limits(self):
        daily = [
            {"ts_code": "600664.SH", "trade_date": "20260901", "pre_close": 10, "name": "哈药股份"},
            {"ts_code": "300001.SZ", "trade_date": "20260901", "pre_close": 10, "name": "特锐德"},
            {"ts_code": "600001.SH", "trade_date": "20260901", "pre_close": 10, "name": "ST测试"},
            {"ts_code": "600002.SH", "trade_date": "20260703", "pre_close": 10, "name": "*ST测试"},
            {"ts_code": "300002.SZ", "trade_date": "20260703", "pre_close": 10, "name": "ST创业"},
        ]
        controls = build_control_rows(daily)
        by_symbol = {row["ts_code"]: row for row in controls["stk_limit"]}
        self.assertEqual(by_symbol["600664.SH"]["up_limit"], "11.00")
        self.assertEqual(by_symbol["300001.SZ"]["up_limit"], "12.00")
        # Main-board ST: 10% from 2026-07-06, 5% before; a ChiNext ST stays 20%.
        self.assertEqual(by_symbol["600001.SH"]["up_limit"], "11.00")
        self.assertEqual(by_symbol["600002.SH"]["up_limit"], "10.50")
        self.assertEqual(by_symbol["300002.SZ"]["up_limit"], "12.00")
        self.assertEqual(controls["adj_factor"], [])


    def test_published_limits_are_used_and_only_a_missing_one_is_derived(self):
        daily = [
            # 2026-10-09: BSE rounds inward, so +30% on 98.85 is published as 128.50, not 128.51.
            {"ts_code": "920438.BJ", "trade_date": "20261009", "pre_close": 98.85, "name": "戈碧迦",
             "up_limit": 128.5, "down_limit": 69.2},
            {"ts_code": "600664.SH", "trade_date": "20261009", "pre_close": 10, "name": "哈药股份"},
        ]
        by_symbol = {row["ts_code"]: row for row in build_control_rows(daily)["stk_limit"]}
        self.assertEqual((by_symbol["920438.BJ"]["up_limit"], by_symbol["920438.BJ"]["down_limit"]), ("128.5", "69.2"))
        self.assertEqual(by_symbol["920438.BJ"]["derivation"], "exchange_published_via_tencent_quote")
        self.assertEqual(by_symbol["600664.SH"]["up_limit"], "11.00")
        self.assertEqual(by_symbol["600664.SH"]["derivation"], "preclose_times_board_limit_ratio")

    def test_the_merge_carries_the_quote_s_published_limits_into_the_daily_row(self):
        vendor = {"600664.SH": {"symbol": "600664.SH", "name": "哈药股份", "close": 9.49}}
        quotes = [{"ts_code": "600664.SH", "trade_date": "20260901", "close": 9.49, "pre_close": 9.29,
                   "up_limit": 10.22, "down_limit": 8.36}]
        row = merge_cross_section(date(2026, 9, 1), vendor, quotes).daily_rows[0]
        self.assertEqual((row["up_limit"], row["down_limit"]), (10.22, 8.36))

if __name__ == "__main__":
    unittest.main()
