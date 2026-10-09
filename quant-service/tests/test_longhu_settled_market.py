from datetime import date
from types import SimpleNamespace
from unittest.mock import patch
import unittest
from app.longhu_settled_market import fetch


class SettledMarketRosterTests(unittest.TestCase):
    def test_gateway_requests_off_plate_equities_and_uses_requested_date(self):
        gateway = object()
        source = SimpleNamespace(
            industry_plate_catalog=lambda: [{"sector_key": "industry", "change_pct": 999}],
            full_market_vendor_rows=lambda *a, **k: ({"600664.SH": {
                "symbol": "600664.SH", "name": "fixture", "plate_id": "industry", "pct_chg": -2,
                "main_net": 123}}, {"symbols": 1}))
        with patch("app.longhu_settled_quotes.fetch", return_value=([], {"received": 0})) as quotes:
            result = fetch(source, date(2026, 10, 9), ["600664.SH", "920002.BJ", "920002.BJ"], quote_source=gateway)
        quotes.assert_called_once_with(gateway, ["600664.SH", "920002.BJ"], date(2026, 10, 9), workers=8)
        self.assertEqual(result["health"]["licensed_ohlc"]["off_plate_requested"], 1)
        self.assertEqual(result["board_rows"][0]["change_pct"], -2)
        self.assertEqual(result["board_rows"][0]["trade_date"], "2026-10-09")
        self.assertEqual(list(result["vendor_rows"]), ["600664.SH"])
