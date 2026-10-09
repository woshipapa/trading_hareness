"""Concept limit strength counted from the Fuyao pool and THS concept membership."""

from __future__ import annotations

import json
import unittest

from app.concept_limit_strength import board_count, concept_strength, event_body, limit_tag

POOL = {
    "600127.SH": {"thscode": "600127.SH", "name": "金健米业", "continue_day_cnt": 3, "max_seal_money": 2.1e8},
    "000981.SZ": {"thscode": "000981.SZ", "name": "山子高科", "continue_day_cnt": 1, "max_seal_money": 8.0e7},
    "002528.SZ": {"thscode": "002528.SZ", "name": "英飞拓", "continue_day_cnt": "1", "max_seal_money": "3.0e8"},
}


class ConceptStrengthTests(unittest.TestCase):
    def test_concepts_rank_by_sealed_members_then_concentration(self):
        memberships = [
            {"sector_key": "885431.TI", "symbol": "000981.SZ"}, {"sector_key": "885431.TI", "symbol": "002528.SZ"},
            {"sector_key": "885566.TI", "symbol": "600127.SH"}, {"sector_key": "885700.TI", "symbol": "000981.SZ"},
            {"sector_key": "885431.TI", "symbol": "600000.SH"},           # a member that did not seal
        ]
        ranked = concept_strength(POOL, memberships, {"885431.TI": 120, "885566.TI": 40, "885700.TI": 10},
                                  {"885431.TI": "新能源汽车", "885566.TI": "大飞机"})
        self.assertEqual([(item["sector_key"], item["limit_up_count"], item["rank"]) for item in ranked],
                         [("885431.TI", 2, 1), ("885700.TI", 1, 2), ("885566.TI", 1, 3)])
        self.assertEqual(ranked[0]["label"], "新能源汽车")
        self.assertEqual(ranked[1]["label"], "885700.TI")          # no label stored: the code, never a guess
        self.assertEqual(ranked[0]["limit_up_ratio"], round(2 / 120, 6))

    def test_leaders_are_highest_board_then_largest_seal(self):
        memberships = [{"sector_key": "885431.TI", "symbol": symbol} for symbol in POOL]
        ranked = concept_strength(POOL, memberships, {"885431.TI": 50}, {})
        self.assertEqual(ranked[0]["limit_up_symbols"], ["600127.SH", "002528.SZ", "000981.SZ"])
        self.assertEqual(ranked[0]["max_board_count"], 3)

    def test_a_member_count_below_the_sealed_count_is_not_trusted(self):
        ranked = concept_strength(POOL, [{"sector_key": "885431.TI", "symbol": "000981.SZ"}], {}, {})
        self.assertEqual((ranked[0]["member_count"], ranked[0]["limit_up_ratio"]), (1, 1.0))

    def test_no_sealed_member_means_no_concept(self):
        self.assertEqual(concept_strength(POOL, [{"sector_key": "885431.TI", "symbol": "600000.SH"}], {}, {}), [])

    def test_board_count_and_tag_read_fuyao_continue_day_cnt(self):
        self.assertEqual(board_count({"continue_day_cnt": "4"}), 4)
        self.assertEqual(board_count({}), 1)
        self.assertEqual((limit_tag({"continue_day_cnt": 1}), limit_tag({"continue_day_cnt": 2})), ("首板", "2天2板"))

    def test_the_stored_pool_body_is_json_text(self):
        body = json.dumps({"thscode": "600127.SH", "continue_day_cnt": 2}, ensure_ascii=False)
        self.assertEqual(event_body({"body": body})["continue_day_cnt"], 2)
        self.assertEqual(event_body({"body": "not json"}), {})
        self.assertEqual(event_body({"body": None}), {})


if __name__ == "__main__":
    unittest.main()
