"""Each board-flow item's net inflow, in CNY, whichever vendor stored it."""

from __future__ import annotations

import unittest

from app.board_flow_units import item_unit, net_inflow_cny
from app.eastmoney_board_flow_curve import intraday_board_flow_curve_items
from app.longhu_board_flow import board_flow_items


class BoardFlowUnitTests(unittest.TestCase):
    def test_new_items_say_their_unit(self):
        public = intraday_board_flow_curve_items("concept", [{"行业": "固态电池", "流入资金": 30.5, "流出资金": 20.0}])
        self.assertEqual((public[0]["unit"], net_inflow_cny(public[0])), ("100m_cny", 1_050_000_000.0))
        longhu = board_flow_items([["881121", "半导体", 643, 4.947, -0.012, 216882285588, 17498330031, 0, 0, 2.152]])
        self.assertEqual((longhu[0]["unit"], net_inflow_cny(longhu[0])), ("cny", 17_498_330_031.0))

    def test_items_stored_without_a_unit_take_their_taxonomy_s(self):
        self.assertEqual(item_unit({"taxonomy_key": "longhu_ths_industry"}, "100m_cny"), "cny")
        self.assertEqual(item_unit({"taxonomy_key": "eastmoney_concept"}), "100m_cny")
        self.assertEqual(item_unit({"taxonomy_key": "unknown"}, "100m_cny"), "100m_cny")
        self.assertIsNone(item_unit({"taxonomy_key": "unknown"}))
        self.assertIsNone(net_inflow_cny({"taxonomy_key": "eastmoney_concept", "net_inflow": True}))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
