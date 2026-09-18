import unittest

from app.longhu_board_flow import TAXONOMY_KEY, board_flow_items, gateway_rows

# Verbatim rows from the gateway on 2026-09-18.
SEMICONDUCTOR = ["881121", "半导体", 643, 4.947, -0.012, 216882285588, 17498330031,
                 96136369141, -78638039110, 2.152, 8512779954901, 3.06]
HOUSEWARES = ["881139", "家居用品", 435, 2.459, -0.032, 6657892465, 5414734,
              1503600082, -1498185348, 1.546, 497272714726, 2.08]


class BoardFlowItemsTests(unittest.TestCase):
    def test_a_ranking_row_maps_onto_the_stored_curve_shape(self):
        items = board_flow_items([SEMICONDUCTOR])
        self.assertEqual(items[0]["taxonomy_key"], TAXONOMY_KEY)
        self.assertEqual(items[0]["sector_key"], "881121")
        self.assertEqual(items[0]["label"], "半导体")
        self.assertEqual(items[0]["net_inflow"], 17498330031.0)
        self.assertEqual(items[0]["change_pct"], 4.947)
        self.assertEqual(items[0]["volume_ratio"], 2.152)

    def test_boards_are_ordered_by_net_inflow(self):
        items = board_flow_items([HOUSEWARES, SEMICONDUCTOR])
        self.assertEqual([item["sector_key"] for item in items], ["881121", "881139"])

    def test_a_row_without_a_net_inflow_is_dropped_not_zeroed(self):
        # Absent flow and balanced flow are different observations, and the
        # rotation detection downstream reads the sign.
        rows = [[*SEMICONDUCTOR[:6], None, *SEMICONDUCTOR[7:]]]
        self.assertEqual(board_flow_items(rows), [])

    def test_a_repeated_board_is_stored_once(self):
        items = board_flow_items([SEMICONDUCTOR, SEMICONDUCTOR])
        self.assertEqual(len(items), 1)

    def test_a_short_or_malformed_row_is_ignored(self):
        self.assertEqual(board_flow_items([["881121", "半导体"], "nonsense", None]), [])

    def test_a_board_with_no_label_falls_back_to_its_key(self):
        items = board_flow_items([["881121", "", 1, 2.0, 0.0, 10.0, 5.0]])
        self.assertEqual(items[0]["label"], "881121")


class GatewayEnvelopeTests(unittest.TestCase):
    def test_rows_are_read_from_the_page_envelope(self):
        payload = {"pages": [{"payload": {"list": [SEMICONDUCTOR]}},
                             {"payload": {"list": [HOUSEWARES]}}]}
        self.assertEqual(len(gateway_rows(payload)), 2)

    def test_a_root_level_list_is_not_mistaken_for_the_body(self):
        # The gateway always nests; a root "list" would be a different contract
        # and silently trusting it is how an empty read looks like a full one.
        self.assertEqual(gateway_rows({"list": [SEMICONDUCTOR]}), [])

    def test_an_empty_envelope_yields_no_rows(self):
        self.assertEqual(gateway_rows({"pages": []}), [])
        self.assertEqual(gateway_rows({}), [])


if __name__ == "__main__":
    unittest.main()
