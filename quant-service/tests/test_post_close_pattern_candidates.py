from __future__ import annotations

from datetime import date
import unittest

from app.post_close_pattern_candidates import select_candidates


class PostClosePatternCandidateTests(unittest.TestCase):
    def test_selects_two_same_limit_ratio_negative_controls_per_positive(self) -> None:
        def features(rows):
            symbol = str(rows[0]["symbol"])
            return {
                "status": "completed", "trading_date": "2026-09-01",
                "limit_pct": 10.0, "volume_multiple_5d": 1.2,
                "ground_to_sky_daily_shape": False,
                "close": 10.0 if symbol == "000001.SZ" else 9.8,
            }

        result = select_candidates(
            date(2026, 9, 1), 1, 1,
            limit_rows=[{"row_data": {"ts_code": "000001.SZ", "tag": "2连板", "name": "正样本"}, "provider_key": "tushare"}],
            step_rows=[{"ts_code": "000001.SZ", "nums": 2}],
            prior_limit_rows=[],
            control_rows=[
                {"symbol": "000002.SZ", "limit_gap_pct": 1.0, "selected_provider": "canonical"},
                {"symbol": "000004.SZ", "limit_gap_pct": 2.0, "selected_provider": "canonical"},
                {"symbol": "000005.SZ", "limit_gap_pct": 3.0, "selected_provider": "canonical"},
            ],
            daily_rows=[
                {"symbol": "000001.SZ", "trading_date": date(2026, 9, 1)},
                {"symbol": "000002.SZ", "trading_date": date(2026, 9, 1)},
                {"symbol": "000004.SZ", "trading_date": date(2026, 9, 1)},
                {"symbol": "000005.SZ", "trading_date": date(2026, 9, 1)},
            ],
            boards={}, lhb_by_symbol={}, focus_symbols=None,
            limit_daily_features=features,
            board_count=lambda tag: 2 if "2" in str(tag) else 0,
        )

        self.assertEqual(result["sample_role_counts"], {"positive_limit_pool": 1, "matched_near_limit_control": 2})
        self.assertEqual([item["symbol"] for item in result["candidates"]], ["000001.SZ", "000002.SZ", "000004.SZ"])
        self.assertTrue(all(item["limit_context"]["sample_role"] == "matched_near_limit_control"
                            for item in result["candidates"][1:]))
        self.assertEqual(result["candidates"][1]["limit_context"]["matched_to_symbol"], "000001.SZ")


if __name__ == "__main__":
    unittest.main()
