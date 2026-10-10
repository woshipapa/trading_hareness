import struct
import unittest

from app.datasources.sources import tdx_f10_finance as f10
from app.datasources.sources.tdx_fin_history import parse_report_file


class TdxF10Fixtures(unittest.TestCase):
    def test_builders_match_wire_layouts(self):
        self.assertEqual(
            f10.build_finance_info_request(0, "000001").hex(),
            "0c1f187600010b000b001000010000303030303031",
        )
        self.assertEqual(
            f10.build_company_categories_request(1, "600519").hex(),
            "0c0f109b00010e000e00cf02010036303035313900000000",
        )
        content = f10.build_company_content_request(0, "000001", "000001.txt", 12, 34)
        self.assertEqual(len(content), 114)
        self.assertEqual(content[10:12], bytes.fromhex("d002"))

    def test_finance_summary_scaling_and_units(self):
        values = [1.5, 2, 3, 20240930, 19910403] + [2] * 30
        body = struct.pack("<HB6s", 1, 0, b"000001") + struct.pack("<fHHII" + "f" * 30, *values)
        row = f10.parse_finance_info(body)
        self.assertEqual(row["code"], "000001")
        self.assertEqual(row["float_shares"], 15000.0)
        self.assertEqual(row["total_shares"], 20000.0)
        self.assertEqual(row["eps"], 2.0)
        self.assertEqual(row["total_assets"], 2000.0)
        self.assertEqual(row["net_profit"], 2000.0)
        self.assertEqual(row["field_units"]["total_assets"], "元")
        self.assertEqual(row["field_units"]["province"], "code")
        self.assertEqual(row["field_units"]["updated_date"], "YYYYMMDD")
        self.assertEqual(row["units"]["amounts"], "元")

    def test_finance_money_fixture_is_thousand_yuan(self):
        values = [0.0, 0, 0, 20260815, 20200101] + [0.0] * 30
        values[12] = 6028785152.0  # raw total_assets, normalized to yuan
        body = struct.pack("<HB6s", 1, 0, b"000001") + struct.pack("<fHHII" + "f" * 30, *values)
        self.assertEqual(f10.parse_finance_info(body)["total_assets"], 6028785152000.0)

    def test_categories_content_and_report(self):
        name = "公司简介".encode("gbk")
        body = struct.pack("<H", 1) + struct.pack("<64s80sII", name, b"000001.txt", 4, 99)
        self.assertEqual(f10.parse_company_categories(body)[0]["name"], "公司简介")
        text = "主营业务：白酒".encode("gbk")
        content = b"\0" * 10 + struct.pack("<H", len(text)) + text + b"tail"
        self.assertEqual(f10.parse_company_content(content), "主营业务：白酒")
        self.assertEqual(parse_report_file(struct.pack("<I", 3) + b"abcjunk"), (3, b"abc"))


if __name__ == "__main__":
    unittest.main()
