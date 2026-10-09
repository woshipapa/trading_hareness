"""同花顺 board names to codes: folding, catalog order, ambiguity, and coverage."""

from __future__ import annotations

import unittest

from app.industry_membership_preference import industry_preference_sql
from app.ths_concept_name_bridge import build_bridge, normalize_board_name

CATALOG = [
    {"taxonomy_key": "fuyao_ths_concept", "sector_key": "886108.TI", "label": "AI应用"},
    {"taxonomy_key": "fuyao_ths_concept", "sector_key": "885431.TI", "label": "新能源汽车"},
    {"taxonomy_key": "ths_concept_flow", "sector_key": "885431.TI", "label": "新能源汽车(旧名)"},
    {"taxonomy_key": "ths_concept_flow", "sector_key": "885959.TI", "label": "PCB"},
    {"taxonomy_key": "ths_concept_flow", "sector_key": "886999.TI", "label": "AI应用"},
    {"taxonomy_key": "fuyao_ths_concept", "sector_key": "885001.TI", "label": "华为概念"},
    {"taxonomy_key": "fuyao_ths_concept", "sector_key": "885002.TI", "label": "华为 概念"},
    {"taxonomy_key": "fuyao_ths_industry", "sector_key": "881121.TI", "label": "半导体"},
]


class NameBridgeTests(unittest.TestCase):
    def test_full_width_spaces_and_brackets_fold_away(self):
        self.assertEqual(normalize_board_name("（ＡＩ 应用）"), "ai应用")
        self.assertEqual(normalize_board_name("【PCB】"), normalize_board_name("pcb"))

    def test_the_fuyao_catalog_wins_and_the_frozen_one_fills_gaps(self):
        bridge = build_bridge("concept", CATALOG)
        self.assertEqual(bridge.code_for("ai应用"), "886108.TI", "the frozen catalog's other code does not override Fuyao")
        self.assertEqual(bridge.code_for("PCB"), "885959.TI", "a board only the frozen catalog names still maps")
        self.assertEqual(bridge.name_for("885431.ti"), "新能源汽车")
        self.assertIsNone(bridge.code_for("半导体"), "an industry is not a concept")

    def test_a_real_name_mismatch_is_reported_not_dropped(self):
        # 同花顺's flow page and its index list do not always spell a board the same way.
        matched, coverage = build_bridge("concept", CATALOG).match(["AI应用", "新能源车", "华为概念", "PCB"])
        self.assertEqual(matched, {"AI应用": "886108.TI", "PCB": "885959.TI"})
        self.assertEqual(coverage["unmatched"], ["新能源车"])
        self.assertEqual(coverage["ambiguous"], ["华为概念"], "two Fuyao codes fold to one name")
        self.assertEqual((coverage["flow_boards"], coverage["matched"]), (4, 2))

    def test_industry_bridge_and_preference(self):
        self.assertEqual(build_bridge("industry", CATALOG).code_for("半导体"), "881121.TI")
        self.assertTrue(industry_preference_sql().startswith("CASE member.taxonomy_key WHEN 'longhu_ths_industry' THEN 0"))



class ConceptDaysTests(unittest.TestCase):
    def test_live_rows_take_codes_and_replace_their_sessions_frozen_rows(self):
        from datetime import date

        from app.ths_concept_name_bridge import concept_days_by_code, theme_board_codes

        rows = [
            {"taxonomy_key": "ths_concept_flow", "sector_key": "886108.TI", "trading_date": date(2026, 10, 8), "net_amount": 3},
            {"taxonomy_key": "ths_concept_flow", "sector_key": "886108.TI", "trading_date": date(2026, 10, 9), "net_amount": 9},
            {"taxonomy_key": "eastmoney_concept", "sector_key": "AI应用", "trading_date": date(2026, 10, 9), "net_amount": 4},
            {"taxonomy_key": "eastmoney_concept", "sector_key": "新能源车", "trading_date": date(2026, 10, 9), "net_amount": 1},
        ]
        keyed, source = concept_days_by_code(rows, build_bridge("concept", CATALOG))
        self.assertEqual([(row["trading_date"].day, row["sector_key"], row["net_amount"]) for row in keyed],
                         [(8, "886108.TI", 3), (9, "886108.TI", 4)])
        self.assertEqual((source["live_sessions"], source["unmatched"]), (1, ["新能源车"]))
        aliases = [{"theme_key": "remote:ai应用", "sector_key": "886108.TI"}, {"theme_key": "remote:ai应用", "sector_key": "885000.TI"}]
        self.assertEqual(theme_board_codes(aliases), {"remote:ai应用": "886108.TI"}, "the first (Fuyao) alias wins")

if __name__ == "__main__":  # pragma: no cover
    unittest.main()
