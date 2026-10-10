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

    def test_fetch_security_list_mocked_sections(self):
        """Test fetch_security_list coverage and warning calculations."""
        # Mock sweep_sync to return fake results
        fake_sweep_result = {
            "host": "127.0.0.1:7709",
            "profile": "login_one",
            "sections": {
                "sz": {
                    "result": [
                        {"market": 0, "code": "000001", "name": "平安", "instrument_type": "stock_main",
                         "list_source": "server_list", "source_host": "127.0.0.1:7709"}
                    ],
                    "rows": 1,
                },
                "sh": {
                    "result": [
                        {"market": 1, "code": "600519", "name": "贵州茅台", "instrument_type": "stock_main",
                         "list_source": "server_list", "source_host": "127.0.0.1:7709"}
                    ],
                    "rows": 1,
                },
                "bj_count": {
                    "result": 10,
                    "rows": 10,
                },
                "zhb": {
                    "result": [
                        {"market": 2, "code": "920000", "name": "测试", "instrument_type": "stock_bj",
                         "list_source": "zhb_tdxbjmore", "source_host": "127.0.0.1:7709"}
                    ],
                    "rows": 1,
                },
            }
        }

        with patch.object(tdx_protocol, 'sweep_sync', return_value=fake_sweep_result):
            evidence = ti.fetch_security_list()

        # Should have 3 rows (1 SZ + 1 SH + 1 BJ)
        self.assertEqual(len(evidence.rows), 3)

        # Coverage should be 3 / (1 + 1 + 10) = 0.25
        self.assertAlmostEqual(evidence.coverage, 0.25)

        # Should have warning about 9 missing BJ rows (10 - 1)
        self.assertTrue(any("bj_missing=9" in w for w in evidence.warnings))

        # Should have host info warning
        self.assertTrue(any("host=127.0.0.1:7709/login_one" in w for w in evidence.warnings))

    def test_fetch_security_list_with_error_sections(self):
        """Test fetch_security_list handles section errors gracefully."""
        fake_sweep_result = {
            "host": "127.0.0.1:7709",
            "profile": "login_one",
            "sections": {
                "sz": {
                    "result": [
                        {"market": 0, "code": "000001", "name": "平安", "instrument_type": "stock_main",
                         "list_source": "server_list", "source_host": "127.0.0.1:7709"}
                    ],
                    "rows": 1,
                },
                "sh": {
                    "error": "Connection timeout"
                },
                "bj_count": {
                    "result": 5,
                    "rows": 5,
                },
                "zhb": {
                    "result": [],
                    "rows": 0,
                },
            }
        }

        with patch.object(tdx_protocol, 'sweep_sync', return_value=fake_sweep_result):
            evidence = ti.fetch_security_list()

        # Should have 1 row (only SZ succeeded)
        self.assertEqual(len(evidence.rows), 1)

        # Coverage should be 1 / (1 + 0 + 5) = 1/6
        self.assertAlmostEqual(evidence.coverage, 1/6, places=5)

        # Should have error warning
        self.assertTrue(any("sh:" in w and "error" in w for w in evidence.warnings))

    def test_instruments_from_security_list(self):
        """Test instruments_from_security_list filters stocks and formats correctly."""
        fake_sweep_result = {
            "host": "127.0.0.1:7709",
            "profile": "login_one",
            "sections": {
                "sz": {"result": [
                    {"market": 0, "code": "000001", "name": "平安", "instrument_type": "stock_main", "is_st": False,
                     "list_source": "server_list", "source_host": "127.0.0.1:7709"},
                    {"market": 0, "code": "000099", "name": "*ST浪潮", "instrument_type": "stock_main", "is_st": True,
                     "list_source": "server_list", "source_host": "127.0.0.1:7709"},
                    {"market": 0, "code": "127045", "name": "可转债", "instrument_type": "cb", "is_st": False,
                     "list_source": "server_list", "source_host": "127.0.0.1:7709"},
                ], "rows": 3},
                "sh": {"result": [
                    {"market": 1, "code": "999999", "name": "指数", "instrument_type": "index", "is_st": False,
                     "list_source": "server_list", "source_host": "127.0.0.1:7709"},
                ], "rows": 1},
                "bj_count": {"result": 0, "rows": 0},
                "zhb": {"result": [], "rows": 0},
            }
        }

        with patch.object(tdx_protocol, 'sweep_sync', return_value=fake_sweep_result):
            instruments = ti.instruments_from_security_list()

        # Should have 3 instruments (3 stocks, no CB, no index)
        self.assertEqual(len(instruments), 3)

        # Check symbols and st flags
        symbols = {i["symbol"] for i in instruments}
        self.assertIn("000001.SZ", symbols)
        self.assertIn("000099.SZ", symbols)

        # Check is_st flag
        for inst in instruments:
            if inst["symbol"] == "000099.SZ":
                self.assertTrue(inst["is_st"])
            else:
                self.assertFalse(inst["is_st"])

        # Check list_date is None
        for inst in instruments:
            self.assertIsNone(inst["list_date"])


if __name__ == "__main__":
    unittest.main()
