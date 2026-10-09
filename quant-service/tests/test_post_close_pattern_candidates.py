from __future__ import annotations

from datetime import date
import unittest

from app.numeric_utils import intraday_number
from app.post_close_evidence import lhb_context
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

    def test_dragon_tiger_list_without_seat_detail_is_described_and_never_scored(self) -> None:
        def select(lhb_by_symbol):
            return select_candidates(
                date(2026, 9, 17), 1, 1,
                limit_rows=[{"row_data": {"ts_code": "601086.SH", "tag": "2连板", "name": "国芳集团"}, "provider_key": "fuyao_ths"}],
                step_rows=[{"ts_code": "601086.SH", "nums": 2}], prior_limit_rows=[], control_rows=[],
                daily_rows=[{"symbol": "601086.SH", "trading_date": date(2026, 9, 17)}],
                boards={}, lhb_by_symbol=lhb_by_symbol, focus_symbols=None,
                limit_daily_features=lambda rows: {"status": "completed", "limit_pct": 10.0, "volume_multiple_5d": 1.0},
                board_count=lambda tag: 2 if "2" in str(tag) else 0,
            )["candidates"][0]

        listed = lhb_context([{"symbol": "601086.SH", "source": "fuyao_ths", "available_at": None, "payload": {
            "capability": "a_share_dragon_tiger_list", "trade_date": "2026-09-17", "name": "国芳集团",
            "net_value": -12599059.02, "buy_value": 99580690.98, "sell_value": 112179750.0, "range_days": 1,
            "hot_money_net_value": -2185780.98}}], number=intraday_number)
        with_list, without_list = select(listed), select({})
        reasons = with_list["limit_context"]["selection_reasons"]
        self.assertIn("龙虎榜净卖1260万", reasons)
        self.assertFalse(any("机构" in reason for reason in reasons))
        # The institution score and risk flag need seat detail the list lacks.
        self.assertEqual(with_list["selection_score"], without_list["selection_score"])
        self.assertNotIn("lhb_institution_net_sell", with_list["risk_flags"])
        self.assertIsNone(with_list["limit_context"]["lhb_context"]["institution_net_buy"])


    def test_fuyao_close_pool_row_keeps_its_provenance_and_seal(self) -> None:
        """The pool row is labelled by its provider, so the seal/float screen reports it unavailable."""
        def features(_rows):
            return {"status": "completed", "trading_date": "2026-09-18", "limit_pct": 10.0,
                    "volume_multiple_5d": 1.6, "ground_to_sky_daily_shape": False}

        row = {"ts_code": "603721.SH", "name": "中广天择", "trade_date": "20260918", "limit_type": "涨停池",
               "status": "涨停", "price": 21.01, "pct_chg": 10.0, "limit_amount": 114685186.0,
               "max_seal_money": 241522556.0, "turnover_rate": None, "open_num": None, "tag": "2连板",
               "lu_desc": "AI语料+传媒内容+控股变更", "limit_up_time": "09:25", "event_type": "limit_up_pool"}
        result = select_candidates(
            date(2026, 9, 18), 4, 2,
            limit_rows=[{"row_data": row, "provider_key": "market_events:fuyao_ths", "available_at": None}],
            step_rows=[{"ts_code": "603721.SH", "nums": 2}], prior_limit_rows=[], control_rows=[],
            daily_rows=[{"symbol": "603721.SH", "trading_date": date(2026, 9, 18)}],
            boards={}, lhb_by_symbol={}, focus_symbols=None,
            limit_daily_features=features, board_count=lambda tag: 2 if "2" in str(tag) else 1,
        )

        context = result["candidates"][0]["limit_context"]
        self.assertEqual(context["provider_key"], "market_events:fuyao_ths")
        self.assertEqual(context["streak_count"], 2)
        self.assertEqual(context["continuation_watch"]["status"], "unavailable")
        self.assertEqual(context["continuation_watch"]["reason"], "missing_tushare_limit_pool_fields")
        self.assertGreater(result["candidates"][0]["selection_score"], 40)

if __name__ == "__main__":
    unittest.main()
