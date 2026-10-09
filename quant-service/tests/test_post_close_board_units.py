"""The post-close board context compares board flows in CNY, whatever unit each source uses."""

from __future__ import annotations

import unittest

from app.post_close_evidence import exact_board_context


class BoardContextUnitTests(unittest.TestCase):
    def test_a_longhu_row_in_yuan_does_not_outrank_a_larger_ths_row_in_yi(self):
        rows = [
            {"symbol": "600001.SH", "sector_key": "固态电池", "net_amount": 12.0, "net_amount_unit": "100m_cny"},
            {"symbol": "600002.SH", "sector_key": "881121", "net_amount": 450_000_000.0, "net_amount_unit": "yuan"},
            {"symbol": "600003.SH", "sector_key": "储能", "net_amount": 2.0, "net_amount_unit": "100m_cny"},
        ]
        contexts = exact_board_context(rows, json_safe=lambda value: value)
        self.assertEqual(contexts["600001.SH"]["flow_percentile"], 1.0, "12亿 is the largest inflow")
        self.assertEqual(contexts["600002.SH"]["flow_percentile"], 0.5)
        self.assertEqual(contexts["600003.SH"]["flow_percentile"], 0.0)
        self.assertEqual(contexts["600002.SH"]["net_amount_cny"], 450_000_000.0)

    def test_each_symbol_keeps_its_strongest_board_in_cny(self):
        rows = [{"symbol": "600001.SH", "sector_key": "A", "net_amount": 300_000_000.0, "net_amount_unit": "yuan"},
                {"symbol": "600001.SH", "sector_key": "B", "net_amount": 4.0, "net_amount_unit": "100m_cny"}]
        self.assertEqual(exact_board_context(rows, json_safe=lambda value: value)["600001.SH"]["sector_key"], "B")

    def test_a_row_without_a_unit_is_the_thsera_yi(self):
        rows = [{"symbol": "600001.SH", "sector_key": "A", "net_amount": 1.5}]
        self.assertEqual(exact_board_context(rows, json_safe=lambda value: value)["600001.SH"]["net_amount_cny"], 150_000_000.0)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
