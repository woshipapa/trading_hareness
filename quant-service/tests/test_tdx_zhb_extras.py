import unittest
from datetime import date
from pathlib import Path

from app.datasources.sources.tdx_files import TdxFileError
from app.datasources.sources.tdx_zhb_extras import (
    inspect_binary_member,
    parse_adr_ah_pairs,
    parse_bj_mapping,
    parse_chain_boards,
    parse_holiday_calendar,
    parse_hspy,
    parse_industry_definitions,
    parse_industry_stock_references,
    parse_ipo_subscriptions,
    parse_tipinfo,
)

TIPINFO = Path(__file__).parent / "fixtures" / "tdx_zhb_20261009" / "tipinfo.dat"


class TdxZhbExtrasTests(unittest.TestCase):
    def test_holiday_and_rule_parser(self):
        result = parse_holiday_calendar(b"[Holiday]\nNUM=1\nY36=2026,1001,1002,\n", b"[RULE]\nCYBZDRatio=0.20\n")
        self.assertEqual(result["holidays"], ["2026-10-01", "2026-10-02"])
        self.assertEqual(result["rules"]["RULE.CYBZDRatio"], "0.20")

    def test_bj_mapping_and_ipo(self):
        self.assertEqual(parse_bj_mapping(b"000000,0,1,20261009,\n44|832000|920000|A|20251009\n"), {"832000": "920000"})
        ipo = parse_ipo_subscriptions(b"0|001381|20261019|11.20||||||||001381||||Name|\n", b"0|301149|123288|96000|9.16|0.01|371149|1000|20260929|0.0017||Bond\n")
        self.assertEqual(ipo["equity"][0]["price"], 11.2)
        self.assertEqual(ipo["other"][0]["bond_code"], "123288")

    def test_industry_tree_and_stock_reference(self):
        rows = parse_industry_definitions(b"#ZJHHY\nA|Agriculture\nA01|Farming\n")
        self.assertEqual(rows[1]["parent_code"], "A")
        refs = parse_industry_stock_references(b"0|000001|A01|\n")
        self.assertEqual(refs[0]["stock_code"], "000001")
        self.assertEqual(refs[0]["industry_codes"], ["A01"])

    def test_remaining_text_shapes(self):
        self.assertEqual(parse_chain_boards(b"880506|CYL00210|5G\n")[0]["board_code"], "880506")
        pairs = parse_adr_ah_pairs(b"Name|03660|QFIN|2\n", b"Name|002594|01211|1\n")
        self.assertEqual(pairs["ah"][0]["a_code"], "002594")
        self.assertEqual(parse_hspy(b"0|002839|ZJGH\n")[0]["abbreviation"], "ZJGH")

    def test_tipinfo_excerpt_gives_each_row_its_first_disclosure_date(self):
        rows = parse_tipinfo(TIPINFO.read_bytes())
        self.assertEqual(len(rows), 60)
        first = rows[0]
        self.assertEqual((first["market"], first["code"], first["report_period"], first["eps"], first["first_disclosure_date"]),
                         ("0", "000001", "20260630", 1.24, date(2026, 8, 15)))
        self.assertEqual(first["raw_fields"], ("0|000001|20260630|1.240000|20260815|20240221|20240221||||20150119|||20180521|"
                                               "25224.80|20150521|59880.24|||||").split("|"))
        self.assertTrue(all(row["first_disclosure_date"].strftime("%Y%m%d") == row["field_4"] for row in rows))

    def test_tipinfo_row_with_a_bad_eps_date_or_width_raises_instead_of_being_skipped(self):
        def row(*fields: str) -> bytes:
            return "|".join(fields + ("",) * (22 - len(fields))).encode() + b"\n"
        good = row("0", "000001", "20260630", "1.24", "20260815")
        with self.assertRaisesRegex(TdxFileError, "000002: EPS 'nan' is not a number"):
            parse_tipinfo(good + row("0", "000002", "20260630", "nan", "20260828"))
        with self.assertRaisesRegex(TdxFileError, "000002: column 4 '2026828' is not a date"):
            parse_tipinfo(good + row("0", "000002", "20260630", "-1.25", "2026828"))
        with self.assertRaisesRegex(TdxFileError, "tipinfo row has 21 columns, not 22"):
            parse_tipinfo(good + good[:-2] + b"\n")

    def test_binary_inspection_does_not_claim_semantics(self):
        result = inspect_binary_member(bytes(range(256)) * 2)
        self.assertEqual(result["prefix_length"], 16)
        self.assertEqual(result["semantic_status"], "UNKNOWN; no decoder claimed")


if __name__ == "__main__":
    unittest.main()
