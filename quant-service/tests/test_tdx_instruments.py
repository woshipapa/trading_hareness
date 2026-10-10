import struct
import unittest

from app.datasources.sources import tdx_instruments as ti


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


if __name__ == "__main__":
    unittest.main()
