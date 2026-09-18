import unittest

from app.board_flow_drill import board_members, drill_board_event, drill_board_events

INFLOW = {"taxonomy_key": "longhu_ths_industry", "sector_key": "881121", "label": "半导体",
          "direction": "inflow", "event_type": "flow_surge",
          "delta_net_inflow": 1.8e10, "change_pct": 4.98}
OUTFLOW = {"taxonomy_key": "longhu_ths_industry", "sector_key": "881139", "label": "家居用品",
           "direction": "outflow", "event_type": "cross_zero",
           "delta_net_inflow": -3.0e9, "change_pct": -2.1}

MEMBERSHIP = {
    "002156.SZ": {"881121"}, "600171.SH": {"881121"}, "603986.SH": {"881121"},
    "603008.SH": {"881139"}, "002572.SZ": {"881139"},
}
QUOTES = {
    "002156.SZ": {"name": "通富微电", "pct_change": 9.8, "turnover": 5.6e9},
    "600171.SH": {"name": "上海贝岭", "pct_change": 7.3, "turnover": 1.2e9},
    "603986.SH": {"name": "兆易创新", "pct_change": 2.0, "turnover": 3.0e9},
    "603008.SH": {"name": "喜临门", "pct_change": -6.4, "turnover": 8.0e8},
    "002572.SZ": {"name": "索菲亚", "pct_change": -0.3, "turnover": 4.0e8},
}


class BoardMembersTests(unittest.TestCase):
    def test_only_exact_membership_maps_a_board(self):
        self.assertEqual(board_members(MEMBERSHIP, "881121"),
                         ["002156.SZ", "600171.SH", "603986.SH"])

    def test_an_unknown_board_has_no_members(self):
        self.assertEqual(board_members(MEMBERSHIP, "999999"), [])


class DrillBoardEventTests(unittest.TestCase):
    """A board surge names a sector; relative strength names the member."""

    def test_an_inflow_board_ranks_by_how_much_a_member_leads_it(self):
        picks = drill_board_event(INFLOW, MEMBERSHIP, QUOTES)
        self.assertEqual([item["symbol"] for item in picks], ["002156.SZ", "600171.SH"])
        self.assertAlmostEqual(picks[0]["relative_strength_pct"], 9.8 - 4.98, places=4)
        self.assertEqual(picks[0]["board_rank"], 1)

    def test_a_member_merely_carried_by_its_board_is_not_a_candidate(self):
        # 兆易创新 is up 2.0 against a board up 4.98: it lagged the move.
        picks = drill_board_event(INFLOW, MEMBERSHIP, QUOTES)
        self.assertNotIn("603986.SH", [item["symbol"] for item in picks])

    def test_a_member_barely_ahead_of_its_board_is_not_a_candidate(self):
        # +5.1 against a board at +4.98 leads by 0.12: inside the noise the
        # threshold exists to exclude.
        quotes = {**QUOTES, "600171.SH": {"name": "上海贝岭", "pct_change": 5.1, "turnover": 1.2e9}}
        self.assertNotIn("600171.SH", [i["symbol"] for i in drill_board_event(INFLOW, MEMBERSHIP, quotes)])

    def test_an_outflow_board_leads_with_the_name_falling_hardest(self):
        picks = drill_board_event(OUTFLOW, MEMBERSHIP, QUOTES)
        self.assertEqual([item["symbol"] for item in picks], ["603008.SH"])
        self.assertAlmostEqual(picks[0]["relative_strength_pct"], 6.4 - 2.1, places=4)

    def test_a_member_moving_against_its_board_is_excluded(self):
        quotes = {**QUOTES, "002156.SZ": {"name": "通富微电", "pct_change": -1.0, "turnover": 5.6e9}}
        self.assertNotIn("002156.SZ", [i["symbol"] for i in drill_board_event(INFLOW, MEMBERSHIP, quotes)])

    def test_turnover_share_is_computed_against_the_quoted_members(self):
        picks = drill_board_event(INFLOW, MEMBERSHIP, QUOTES)
        self.assertAlmostEqual(picks[0]["turnover_share"], 5.6e9 / (5.6e9 + 1.2e9 + 3.0e9), places=6)

    def test_an_unquoted_member_is_skipped_without_failing_the_board(self):
        quotes = {k: v for k, v in QUOTES.items() if k != "002156.SZ"}
        picks = drill_board_event(INFLOW, MEMBERSHIP, quotes)
        self.assertEqual([item["symbol"] for item in picks], ["600171.SH"])

    def test_a_board_with_no_direction_yields_nothing(self):
        self.assertEqual(drill_board_event({**INFLOW, "direction": ""}, MEMBERSHIP, QUOTES), [])

    def test_nothing_here_is_decision_eligible(self):
        self.assertFalse(drill_board_event(INFLOW, MEMBERSHIP, QUOTES)[0]["decision_eligible"])


class DrillBoardEventsTests(unittest.TestCase):
    def test_boards_are_ordered_by_the_size_of_their_flow_move(self):
        result = drill_board_events([OUTFLOW, INFLOW], MEMBERSHIP, QUOTES)
        self.assertEqual(result["candidates"][0]["sector_key"], "881121")
        self.assertEqual(result["boards_drilled"], 2)

    def test_a_board_the_membership_cannot_resolve_is_reported_not_dropped(self):
        # Silently returning fewer names would present a coverage gap as a
        # quiet sector.
        event = {**INFLOW, "sector_key": "999999", "label": "未知板块"}
        result = drill_board_events([event], MEMBERSHIP, QUOTES)
        self.assertEqual(result["boards_without_membership"], ["999999"])
        self.assertEqual(result["candidates"], [])

    def test_one_crowded_board_cannot_fill_the_whole_shortlist(self):
        membership = {f"{600000 + i}.SH": {"881121"} for i in range(30)}
        quotes = {f"{600000 + i}.SH": {"pct_change": 20.0 - i * 0.1, "turnover": 1e8} for i in range(30)}
        result = drill_board_events([INFLOW], membership, quotes, max_per_board=5)
        self.assertEqual(len(result["candidates"]), 5)

    def test_the_total_shortlist_is_bounded(self):
        events = [{**INFLOW, "sector_key": str(881100 + i)} for i in range(10)]
        membership = {f"{600000 + i}.SH": {str(881100 + i % 10)} for i in range(60)}
        quotes = {f"{600000 + i}.SH": {"pct_change": 15.0, "turnover": 1e8} for i in range(60)}
        result = drill_board_events(events, membership, quotes, max_total=8)
        self.assertEqual(len(result["candidates"]), 8)


if __name__ == "__main__":
    unittest.main()


class DeliverySelectionTests(unittest.TestCase):
    """The same leader re-qualifies every minute its board stays hot."""

    @staticmethod
    def _candidate(symbol, sector="881121", direction="inflow"):
        return {"symbol": symbol, "taxonomy_key": "longhu_ths_industry",
                "sector_key": sector, "direction": direction}

    def test_a_finding_already_sent_is_not_sent_again(self):
        from app.board_flow_drill import delivery_key, select_for_delivery
        first = self._candidate("002156.SZ")
        result = select_for_delivery([first], [delivery_key(first)])
        self.assertEqual(result["selected"], [])
        self.assertEqual(result["suppressed_repeat"], 1)

    def test_the_same_name_in_another_board_is_a_separate_finding(self):
        from app.board_flow_drill import delivery_key, select_for_delivery
        first = self._candidate("002156.SZ", sector="881121")
        other = self._candidate("002156.SZ", sector="881270")
        result = select_for_delivery([other], [delivery_key(first)])
        self.assertEqual(len(result["selected"]), 1)

    def test_a_reversal_of_direction_is_a_separate_finding(self):
        from app.board_flow_drill import delivery_key, select_for_delivery
        inflow = self._candidate("002156.SZ")
        outflow = self._candidate("002156.SZ", direction="outflow")
        result = select_for_delivery([outflow], [delivery_key(inflow)])
        self.assertEqual(len(result["selected"]), 1)

    def test_one_pass_cannot_exceed_its_own_budget(self):
        from app.board_flow_drill import select_for_delivery
        picks = [self._candidate(f"{600000 + i}.SH") for i in range(10)]
        self.assertEqual(len(select_for_delivery(picks, [], max_per_pass=3)["selected"]), 3)

    def test_the_session_budget_is_derived_from_what_was_already_sent(self):
        # A restart must not hand out a fresh allowance.
        from app.board_flow_drill import select_for_delivery
        picks = [self._candidate(f"{600000 + i}.SH") for i in range(10)]
        sent = [f"{600100 + i}.SH:longhu_ths_industry:881121:inflow" for i in range(23)]
        result = select_for_delivery(picks, sent, max_per_pass=3, max_per_session=24)
        self.assertEqual(len(result["selected"]), 1)
        self.assertEqual(result["remaining_after"], 0)

    def test_an_exhausted_session_sends_nothing_more(self):
        from app.board_flow_drill import select_for_delivery
        sent = [f"{600100 + i}.SH:longhu_ths_industry:881121:inflow" for i in range(24)]
        result = select_for_delivery([self._candidate("002156.SZ")], sent, max_per_session=24)
        self.assertEqual(result["selected"], [])

    def test_each_selected_finding_carries_its_key(self):
        from app.board_flow_drill import select_for_delivery
        selected = select_for_delivery([self._candidate("002156.SZ")], [])["selected"]
        self.assertEqual(selected[0]["delivery_key"], "002156.SZ:longhu_ths_industry:881121:inflow")


class AttachNamesTests(unittest.TestCase):
    def test_a_missing_name_is_resolved_from_the_instrument_table(self):
        from app.board_flow_drill import attach_names
        resolved = attach_names([{"symbol": "002156.SZ", "name": None}], {"002156.SZ": "通富微电"})
        self.assertEqual(resolved[0]["name"], "通富微电")

    def test_a_name_already_present_is_kept(self):
        from app.board_flow_drill import attach_names
        resolved = attach_names([{"symbol": "002156.SZ", "name": "通富微电"}], {"002156.SZ": "别的"})
        self.assertEqual(resolved[0]["name"], "通富微电")

    def test_an_unresolvable_symbol_falls_back_to_its_code(self):
        # A six-digit code is a poor label but an honest one; inventing a name
        # would be worse.
        from app.board_flow_drill import attach_names
        self.assertEqual(attach_names([{"symbol": "002156.SZ"}], {})[0]["name"], "002156.SZ")
