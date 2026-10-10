import struct
import unittest
from unittest.mock import patch

from app.datasources.sources import tdx_instruments as ti, tdx_protocol


class InstrumentTests(unittest.TestCase):
    def test_security_request_and_rows(self):
        self.assertEqual(ti.build_security_count_request(0)[10:12], b"N\x04")
        self.assertEqual(ti.build_security_list_request(1, 1000).hex(), "0c01186401010600060050040100e803")
        row = struct.pack("<6sH8s4sBI4s", b"688001", 100, "ST测试".encode("gbk").ljust(8, b"\0"), b"\0" * 4, 3, 123456, b"\0" * 4)
        parsed = ti.parse_security_list(struct.pack("<H", 1) + row, market=1)
        self.assertEqual(parsed[0]["code"], "688001")
        self.assertEqual(parsed[0]["name"], "ST测试")
        self.assertEqual(parsed[0]["pre_close"], 123.456)

    def test_taxonomy_covers_requested_families(self):
        cases = [(1, "600000", "平安" , "stock_main"), (1, "688001", "科创", "stock_star"),
                 (0, "300001", "创业", "stock_chinext"), (2, "920000", "北证", "stock_bj"),
                 (1, "510300", "ETF", "etf"), (0, "127045", "转债", "cb"),
                 (1, "999999", "指数", "index"), (1, "880761", "板块", "board")]
        for market, code, name, expected in cases:
            self.assertEqual(ti.instrument_type(market, code, name), expected)
        self.assertTrue(ti.classify_instrument(1, "600000", "*ST风险")["is_st"])

    def test_scale_is_list_decimal_point(self):
        quote = {"price": 11697.1, "last_close": 11600.0, "bid1": 11700.0}
        self.assertAlmostEqual(ti.scale_quote(quote, 4)["price"], 116.971)
        self.assertAlmostEqual(ti.scale_quote({"price": 43.85}, 3)["price"], 4.385)
        self.assertEqual(ti.price_scale(2), 100.0)

    def test_index_float_bars_with_breadth(self):
        record = struct.pack("<IfffffIIHH", 20261009, 3800.0, 3810.0, 3790.0, 3805.0, 123456.0, 789, 0, 1200, 800)
        rows = ti.parse_index_bars(struct.pack("<H", 1) + record)
        self.assertEqual(rows[0]["datetime"], "2026-10-09")
        self.assertEqual((rows[0]["up_count"], rows[0]["down_count"]), (1200, 800))
        self.assertEqual(rows[0]["close"], 3805.0)

    def test_index_compressed_bars_with_breadth(self):
        def enc(value):
            negative = value < 0
            value = abs(value)
            first = value & 0x3f
            value >>= 6
            out = bytearray([first | (0x40 if negative else 0) | (0x80 if value else 0)])
            while value:
                out.append((value & 0x7f) | (0x80 if value >> 7 else 0))
                value >>= 7
            return bytes(out)
        body = struct.pack("<H", 1) + struct.pack("<I", 20261009)
        body += enc(3800000) + enc(5000) + enc(8000) + enc(-3000)
        body += struct.pack("<IIHH", 1, 2, 10, 5)
        row = ti.parse_index_bars(body)
        self.assertEqual(row[0]["datetime"], "2026-10-09")
        self.assertEqual((row[0]["up_count"], row[0]["down_count"]), (10, 5))

    def test_bj_config_mapping_tolerates_delimiters(self):
        mapping = ti.parse_bj_mapping("832000|920000\n920001=830001\n# ignored\n".encode())
        self.assertEqual(mapping, {"832000": "920000", "830001": "920001"})
        self.assertEqual(ti.normalize_bj_symbol("832000.BJ", mapping), "920000.BJ")

    def test_tdxbjmore_rows_are_source_tagged(self):
        rows = ti.parse_tdxbjmore("44|920000|2|安徽凤凰|1|\n".encode("gbk"))
        self.assertEqual(rows[0]["source"], "zhb_tdxbjmore")
        self.assertEqual(rows[0]["name"], "安徽凤凰")

    def test_bar_layout_for_instrument_types(self):
        # ETF: stock layout
        self.assertEqual(ti.bar_layout("etf"), "stock")
        # Convertible bond: stock layout
        self.assertEqual(ti.bar_layout("cb"), "stock")
        # Beijing stock: stock layout
        self.assertEqual(ti.bar_layout("stock_bj"), "stock")
        # STAR: stock layout
        self.assertEqual(ti.bar_layout("stock_star"), "stock")
        # ChiNext: stock layout
        self.assertEqual(ti.bar_layout("stock_chinext"), "stock")
        # Main board: stock layout
        self.assertEqual(ti.bar_layout("stock_main"), "stock")
        # Index: index layout
        self.assertEqual(ti.bar_layout("index"), "index")
        # Board: index layout
        self.assertEqual(ti.bar_layout("board"), "index")

    def _sweep(self, *, sz=None, zhb=None):
        """A synthetic sweep result in tdx_protocol.sweep_sync's shape."""
        def server(market, code, name):
            return {"market": market, "code": code, "name": name, "decimal_point": 2, "pre_close": 10.0}

        return {"host": "1.2.3.4:7709", "profile": "login_one", "sections": {
            "SZ": sz or {"result": (3, [server(0, "000001", "平安银行"), server(0, "000099", "*ST测试")])},
            "SH": {"result": (1, [server(1, "999999", "上证指数")])},
            "BJ": {"result": 2},
            "zhb": zhb or {"result": [{"market": 44, "code": "920000", "name": "安徽凤凰"}]},
        }}

    def test_security_list_counts_against_the_server_and_flags_the_bj_gap(self):
        import asyncio
        with patch.object(tdx_protocol, "sweep_sync", return_value=self._sweep()):
            evidence = asyncio.run(ti.fetch_security_list())
        self.assertAlmostEqual(evidence.coverage, 4 / 6, msg="SZ said 3 and sent 2: a missing page lowers coverage")
        self.assertIn("bj_missing=1", evidence.warnings)
        self.assertIn("host=1.2.3.4:7709/login_one", evidence.warnings)
        rows = {row["symbol"]: row for row in evidence.rows}
        self.assertEqual(rows["920000.BJ"]["market"], 2, "tdxbjmore's own market number 44 is not the protocol market")
        self.assertEqual((rows["920000.BJ"]["decimal_point"], rows["920000.BJ"]["list_source"]), (None, "zhb_tdxbjmore"))
        self.assertEqual((rows["000099.SZ"]["is_st"], rows["999999.SH"]["instrument_type"]), (True, "index"))
        self.assertEqual(rows["000001.SZ"]["source_host"], "1.2.3.4:7709/login_one")

    def test_a_lost_market_fails_the_attempt_and_a_lost_zhb_only_lowers_coverage(self):
        import asyncio
        with patch.object(tdx_protocol, "sweep_sync", return_value=self._sweep(sz={"error": "timeout"})):
            with self.assertRaises(tdx_protocol.TdxProtocolError):
                asyncio.run(ti.fetch_security_list())
        with patch.object(tdx_protocol, "sweep_sync", return_value=self._sweep(zhb={"error": "closed"})):
            evidence = asyncio.run(ti.fetch_security_list())
        self.assertIn("zhb_failed: closed", evidence.warnings)
        self.assertIn("bj_missing=2", evidence.warnings)
        self.assertAlmostEqual(evidence.coverage, 3 / 6)

    def test_instruments_keep_stocks_only_and_never_invent_a_list_date(self):
        import asyncio
        with patch.object(tdx_protocol, "sweep_sync", return_value=self._sweep()):
            evidence = asyncio.run(ti.fetch_instruments())
        self.assertEqual({row["symbol"] for row in evidence.rows}, {"000001.SZ", "000099.SZ", "920000.BJ"})
        self.assertTrue(all(row["list_date"] is None for row in evidence.rows))
        self.assertAlmostEqual(evidence.coverage, 4 / 6)
if __name__ == "__main__":
    unittest.main()
