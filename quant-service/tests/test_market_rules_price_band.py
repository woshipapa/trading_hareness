import unittest
from datetime import date

from app.ashare_reality import price_limit_state
from app.live_policy import live_policy_gate
from app.market_rules import a_share_board, a_share_limit_ratio
from app.watchlist_daily_factors import daily_factors_from_rows


class PriceBandTests(unittest.TestCase):
    def test_every_board_prefix_gets_its_band(self):
        cases = {
            "600000.SH": 0.10, "000001.SZ": 0.10, "002594.SZ": 0.10,
            "300750.SZ": 0.20, "301000.SZ": 0.20, "302132.SZ": 0.20,
            "688981.SH": 0.20, "689009.SH": 0.20,
            "830799.BJ": 0.30, "430047.BJ": 0.30, "920819.BJ": 0.30,
        }
        for symbol, ratio in cases.items():
            with self.subTest(symbol=symbol):
                self.assertEqual(a_share_limit_ratio(symbol, False, date(2026, 9, 25)), ratio)

    def test_st_keeps_the_growth_board_band(self):
        # The ST 5% band was only ever a main-board rule.
        for symbol in ("300001.SZ", "302001.SZ", "688001.SH", "689009.SH"):
            self.assertEqual(a_share_limit_ratio(symbol, True, date(2025, 1, 2)), 0.20)
        self.assertEqual(a_share_limit_ratio("920819.BJ", True, date(2025, 1, 2)), 0.30)

    def test_main_board_st_band_follows_the_2026_07_06_rule_change(self):
        self.assertEqual(a_share_limit_ratio("600001.SH", True, date(2026, 7, 3)), 0.05)
        self.assertEqual(a_share_limit_ratio("600001.SH", True, "2026-07-06"), 0.10)
        self.assertEqual(a_share_limit_ratio("000001.SZ", True, "20260707"), 0.10)

    def test_board_classifier(self):
        self.assertEqual(a_share_board("920819.BJ"), "beijing")
        self.assertEqual(a_share_board("302132.SZ"), "registration")
        self.assertEqual(a_share_board("601318.SH"), "main")

    def test_beijing_920_code_is_not_treated_as_main_board(self):
        state = price_limit_state(symbol="920819.BJ", quote={"pct_change": 12.0, "price_trade_date": "20260925"})
        self.assertFalse(state["at_limit_up"])
        self.assertEqual(state["limit_ratio"], 0.30)


class StaleLimitTests(unittest.TestCase):
    def _factors(self, last_date: date, limit_up: float) -> dict:
        rows = [{"trading_date": date(2026, 8, 1 + (index % 28)), "high": 10, "low": 9, "close": 9.5,
                 "volume": 1000, "adj_factor": 1, "is_suspended": False, "limit_up": None,
                 "limit_down": None, "is_st": False} for index in range(30)]
        rows[-1].update({"trading_date": last_date, "limit_up": limit_up, "limit_down": 8.55})
        return daily_factors_from_rows(rows, number=lambda value: None if value is None else float(value))

    def test_prior_session_limits_are_not_applied_to_today(self):
        # Yesterday closed 9.50 against a 10.00 pre-close, so its stored limit
        # is 11.00.  Today's real limit is 10.45; a quote sealed there must
        # read as limit-up, not as buyable under yesterday's band.
        factors = self._factors(date(2026, 9, 24), 11.0)
        self.assertEqual(factors["trade_constraints"]["limit_trading_date"], date(2026, 9, 24))
        gate = live_policy_gate(
            {"signal_type": "entry"}, {"symbol": "600000.SH"},
            {"price": 10.45, "pct_change": 10.0, "price_source": "tencent_batched_watch_quote",
             "price_freshness": {"status": "fresh"}, "price_trade_date": "20260925"},
            factors, {"status": "available", "market_state": "mixed_or_neutral"}, {"status": "confirmed"},
        )
        self.assertIsNone(gate["price_limit_state"]["limit_up"])
        self.assertTrue(gate["price_limit_state"]["at_limit_up"])

    def test_same_session_limits_are_still_used(self):
        factors = self._factors(date(2026, 9, 25), 10.45)
        gate = live_policy_gate(
            {"signal_type": "entry"}, {"symbol": "600000.SH"},
            {"price": 10.45, "pct_change": 10.0, "price_source": "tencent_batched_watch_quote",
             "price_freshness": {"status": "fresh"}, "price_trade_date": "20260925"},
            factors, {"status": "available", "market_state": "mixed_or_neutral"}, {"status": "confirmed"},
        )
        self.assertEqual(gate["price_limit_state"]["limit_up"], 10.45)


if __name__ == "__main__":
    unittest.main()
