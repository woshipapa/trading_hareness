import unittest

from app.paper_auto_execution import (
    exit_quantity, non_fill_reason_counts, plan_paper_orders, position_size, stop_loss_hit, strategy_intent,
)

# A xiaojie_leader_flow candidate carries its own sizing, stop and exit.
XIAOJIE = {
    "symbol": "301583.SZ", "strategy_key": "xiaojie_leader_flow", "decision": "research_candidate",
    "mode": "潜龙出海_swing",
    "position": {"target_fraction": 0.15, "initial_fraction": 0.09, "staged_entry": True},
    "stop_loss": {"mode": "swing", "min_pct": 8.0, "max_pct": 12.0},
    "exit": {"action": "hold_or_wait", "codes": []},
    "risk_flags": [],
}
# A drill candidate carries none, and says so.
DRILL = {"symbol": "688200.SH", "source": "board_flow_drill", "direction": "inflow",
         "large_order": {"confirmed": True}}
QUOTES = {"301583.SZ": {"price": 142.56}, "688200.SH": {"price": 400.03},
          "002156.SZ": {"price": 61.65}}


class StrategyIntentTests(unittest.TestCase):
    """Sizing and exits belong to the strategy, not to this module."""

    def test_non_fill_reasons_are_aggregated_for_research_evidence(self):
        self.assertEqual(
            non_fill_reason_counts([
                {"reason": "insufficient_paper_cash"},
                {"reason": "insufficient_paper_cash"},
                {},
            ]),
            {"insufficient_paper_cash": 2, "unknown": 1},
        )

    def test_the_strategy_supplies_its_own_entry_fraction(self):
        intent = strategy_intent(XIAOJIE)
        self.assertEqual(intent["entry_fraction"], 0.09)
        self.assertEqual(intent["sizing_source"], "strategy")

    def test_a_staged_entry_opens_only_its_first_leg(self):
        # A strategy that builds in two steps must not have its full target
        # opened on the first signal.
        intent = strategy_intent(XIAOJIE)
        self.assertLess(intent["entry_fraction"], intent["target_fraction"])

    def test_the_strategy_stop_is_carried_through(self):
        self.assertEqual(strategy_intent(XIAOJIE)["stop_loss_pct"], 8.0)

    def test_a_source_without_sizing_declares_the_fallback(self):
        intent = strategy_intent(DRILL)
        self.assertEqual(intent["sizing_source"], "fallback_weight")
        self.assertEqual(intent["strategy_key"], "board_flow_drill")


class PositionSizeTests(unittest.TestCase):
    def test_a_size_is_whole_board_lots_inside_the_fraction(self):
        self.assertEqual(position_size(1_000_000, 142.56, 0.09), 600)

    def test_a_name_whose_lot_exceeds_the_fraction_is_unsizeable(self):
        self.assertEqual(position_size(10_000, 400.0, 0.10), 0)


class ExitTests(unittest.TestCase):
    def test_a_full_exit_closes_the_sellable_quantity(self):
        self.assertEqual(exit_quantity({"sellable_quantity": 600}, "exit"), 600)

    def test_a_half_reduction_closes_half_in_whole_lots(self):
        self.assertEqual(exit_quantity({"sellable_quantity": 700}, "reduce_half"), 300)

    def test_a_hold_closes_nothing(self):
        self.assertEqual(exit_quantity({"sellable_quantity": 600}, "hold_or_wait"), 0)

    def test_nothing_sellable_closes_nothing(self):
        # T+1: claiming otherwise would make the simulation more permissive
        # than the market.
        self.assertEqual(exit_quantity({"sellable_quantity": 0}, "exit"), 0)

    def test_the_strategy_stop_is_measured_against_the_simulated_cost(self):
        self.assertTrue(stop_loss_hit({"average_cost": 100.0}, 91.0, 8.0))
        self.assertFalse(stop_loss_hit({"average_cost": 100.0}, 95.0, 8.0))

    def test_a_position_with_no_cost_basis_is_not_stopped_on_a_guess(self):
        self.assertFalse(stop_loss_hit({"average_cost": 0}, 50.0, 8.0))


class PlanPaperOrdersTests(unittest.TestCase):
    def test_a_strategy_candidate_is_bought_at_its_own_size(self):
        plan = plan_paper_orders([XIAOJIE], {}, QUOTES, equity=1_000_000, cash=1_000_000)
        self.assertEqual(plan["buys"][0]["quantity"], 600)
        self.assertEqual(plan["buys"][0]["strategy_key"], "xiaojie_leader_flow")
        self.assertEqual(plan["buys"][0]["sizing_source"], "strategy")
        self.assertEqual(plan["buys"][0]["mode"], "潜龙出海_swing")

    def test_a_strategy_that_said_no_trade_is_not_bought(self):
        no_trade = {**XIAOJIE, "decision": "no_trade"}
        plan = plan_paper_orders([no_trade], {}, QUOTES, equity=1_000_000, cash=1_000_000)
        self.assertEqual(plan["buys"], [])
        self.assertEqual(plan["skipped"][0]["reason"], "strategy_decision:no_trade")

    def test_an_unconfirmed_drill_candidate_is_not_bought(self):
        unconfirmed = {**DRILL, "large_order": {"confirmed": False}}
        plan = plan_paper_orders([unconfirmed], {}, QUOTES, equity=1_000_000, cash=1_000_000)
        self.assertEqual(plan["skipped"][0]["reason"], "large_order_unconfirmed")

    def test_a_strategy_exit_action_sells_the_holding(self):
        exiting = {**XIAOJIE, "exit": {"action": "exit", "codes": ["ma5_break_unrecovered"]}}
        positions = {"301583.SZ": {"quantity": 600, "sellable_quantity": 600, "average_cost": 130.0}}
        plan = plan_paper_orders([exiting], positions, QUOTES, equity=1_000_000, cash=0)
        self.assertEqual(plan["sells"][0]["quantity"], 600)
        self.assertEqual(plan["sells"][0]["reason"], "strategy_exit:exit")
        self.assertEqual(plan["sells"][0]["exit_codes"], ["ma5_break_unrecovered"])

    def test_a_half_reduction_leaves_the_position_open(self):
        reducing = {**XIAOJIE, "exit": {"action": "reduce_half", "codes": ["no_new_high_3d"]}}
        positions = {"301583.SZ": {"quantity": 600, "sellable_quantity": 600, "average_cost": 130.0}}
        plan = plan_paper_orders([reducing], positions, QUOTES, equity=1_000_000, cash=0)
        self.assertEqual(plan["sells"][0]["quantity"], 300)
        self.assertEqual(plan["open_positions"], 1)

    def test_the_strategys_own_stop_closes_a_losing_holding(self):
        positions = {"301583.SZ": {"quantity": 600, "sellable_quantity": 600, "average_cost": 160.0}}
        plan = plan_paper_orders([XIAOJIE], positions, QUOTES, equity=1_000_000, cash=0)
        self.assertEqual(plan["sells"][0]["reason"], "strategy_stop_loss")

    def test_a_name_already_held_is_not_bought_again(self):
        positions = {"301583.SZ": {"quantity": 600, "sellable_quantity": 0, "average_cost": 140.0}}
        plan = plan_paper_orders([XIAOJIE], positions, QUOTES, equity=1_000_000, cash=1_000_000)
        self.assertEqual(plan["skipped"][0]["reason"], "already_held")

    def test_one_pass_cannot_fill_the_whole_book(self):
        picks = [{**XIAOJIE, "symbol": s} for s in ("301583.SZ", "688200.SH", "002156.SZ")]
        plan = plan_paper_orders(picks, {}, QUOTES, equity=1_000_000, cash=1_000_000, max_new_per_pass=2)
        self.assertEqual(len(plan["buys"]), 2)

    def test_cash_is_spent_only_once_across_a_pass(self):
        picks = [{**XIAOJIE, "symbol": s} for s in ("301583.SZ", "002156.SZ")]
        plan = plan_paper_orders(picks, {}, QUOTES, equity=1_000_000, cash=90_000, max_new_per_pass=2)
        self.assertLessEqual(sum(item["notional"] for item in plan["buys"]), 90_000)
        self.assertGreaterEqual(plan["cash_after_plan"], 0)

    def test_selling_out_frees_a_slot_within_the_same_pass(self):
        exiting = [{**XIAOJIE, "symbol": f"{600000 + i}.SH",
                    "exit": {"action": "exit", "codes": []}} for i in range(8)]
        positions = {f"{600000 + i}.SH": {"quantity": 100, "sellable_quantity": 100,
                                          "average_cost": 10.0} for i in range(8)}
        quotes = {**QUOTES, **{f"{600000 + i}.SH": {"price": 10.0} for i in range(8)}}
        plan = plan_paper_orders([*exiting, XIAOJIE], positions, quotes, equity=1_000_000, cash=1_000_000)
        self.assertEqual(len(plan["sells"]), 8)
        self.assertEqual(len(plan["buys"]), 1)
        self.assertEqual(plan["buys"][0]["symbol"], "301583.SZ")

    def test_a_name_the_strategy_is_closing_is_never_opened_in_the_same_pass(self):
        # Acting on both halves of one contradictory instruction would have the
        # ledger buy what the strategy just asked to sell.
        exiting = {**XIAOJIE, "exit": {"action": "exit", "codes": ["support_break"]}}
        plan = plan_paper_orders([exiting], {}, QUOTES, equity=1_000_000, cash=1_000_000)
        self.assertEqual(plan["buys"], [])
        self.assertEqual(plan["skipped"][0]["reason"], "strategy_is_exiting:exit")

    def test_the_plan_states_it_is_a_simulation(self):
        self.assertIn("no broker client", plan_paper_orders([], {}, {}, equity=0, cash=0)["boundary"])


if __name__ == "__main__":
    unittest.main()
