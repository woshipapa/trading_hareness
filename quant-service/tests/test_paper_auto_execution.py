import unittest

from app.paper_auto_execution import exit_reason, plan_paper_orders, position_size

CONFIRMED = {"symbol": "002156.SZ", "direction": "inflow", "board_label": "半导体",
             "relative_strength_pct": 4.82, "large_order": {"confirmed": True}}
UNCONFIRMED = {"symbol": "301419.SZ", "direction": "inflow", "board_label": "通信设备",
               "large_order": {"confirmed": False}}
QUOTES = {"002156.SZ": {"price": 61.65}, "301419.SZ": {"price": 20.0},
          "600171.SH": {"price": 30.0}, "603986.SH": {"price": 100.0}}


class PositionSizeTests(unittest.TestCase):
    def test_a_size_is_whole_board_lots_inside_the_weight(self):
        # 1,000,000 * 10% = 100,000; at 61.65 a lot costs 6,165.
        self.assertEqual(position_size(1_000_000, 61.65), 1600)

    def test_a_name_whose_lot_exceeds_the_weight_is_unsizeable(self):
        self.assertEqual(position_size(10_000, 200.0), 0)

    def test_a_worthless_price_cannot_be_sized(self):
        self.assertEqual(position_size(1_000_000, 0), 0)


class ExitReasonTests(unittest.TestCase):
    def test_a_loss_past_the_stop_closes_the_position(self):
        self.assertEqual(exit_reason({"average_cost": 100.0}, 92.0), "stop_loss")

    def test_a_gain_past_the_target_closes_the_position(self):
        self.assertEqual(exit_reason({"average_cost": 100.0}, 119.0), "take_profit")

    def test_a_position_inside_both_bounds_is_held(self):
        self.assertIsNone(exit_reason({"average_cost": 100.0}, 105.0))

    def test_a_position_with_no_cost_basis_is_not_closed_on_a_guess(self):
        self.assertIsNone(exit_reason({"average_cost": 0}, 105.0))


class PlanPaperOrdersTests(unittest.TestCase):
    """Every rule here is a bound, never a forecast."""

    def test_a_confirmed_inflow_candidate_is_bought(self):
        plan = plan_paper_orders([CONFIRMED], {}, QUOTES, equity=1_000_000, cash=1_000_000)
        self.assertEqual(len(plan["buys"]), 1)
        self.assertEqual(plan["buys"][0]["symbol"], "002156.SZ")
        self.assertEqual(plan["buys"][0]["quantity"], 1600)

    def test_an_unconfirmed_candidate_is_not_bought(self):
        plan = plan_paper_orders([UNCONFIRMED], {}, QUOTES, equity=1_000_000, cash=1_000_000)
        self.assertEqual(plan["buys"], [])
        self.assertEqual(plan["skipped"][0]["reason"], "large_order_unconfirmed")

    def test_an_outflow_candidate_is_never_bought(self):
        outflow = {**CONFIRMED, "direction": "outflow"}
        plan = plan_paper_orders([outflow], {}, QUOTES, equity=1_000_000, cash=1_000_000)
        self.assertEqual(plan["skipped"][0]["reason"], "not_an_inflow_candidate")

    def test_a_name_already_held_is_not_bought_again(self):
        # A hot board re-qualifies its leader every minute; averaging up on
        # that is the scan cadence talking.
        positions = {"002156.SZ": {"sellable_quantity": 0, "average_cost": 60.0}}
        plan = plan_paper_orders([CONFIRMED], positions, QUOTES, equity=1_000_000, cash=1_000_000)
        self.assertEqual(plan["buys"], [])
        self.assertEqual(plan["skipped"][0]["reason"], "already_held")

    def test_one_pass_cannot_fill_the_whole_book(self):
        picks = [{**CONFIRMED, "symbol": s} for s in ("002156.SZ", "600171.SH", "603986.SH")]
        plan = plan_paper_orders(picks, {}, QUOTES, equity=1_000_000, cash=1_000_000, max_new_per_pass=2)
        self.assertEqual(len(plan["buys"]), 2)
        self.assertEqual(plan["skipped"][-1]["reason"], "position_budget_exhausted")

    def test_a_full_book_opens_nothing(self):
        positions = {f"{600000 + i}.SH": {"sellable_quantity": 0, "average_cost": 10.0} for i in range(8)}
        plan = plan_paper_orders([CONFIRMED], positions, QUOTES, equity=1_000_000, cash=1_000_000)
        self.assertEqual(plan["open_slots"], 0)
        self.assertEqual(plan["buys"], [])

    def test_cash_is_spent_only_once_across_a_pass(self):
        picks = [{**CONFIRMED, "symbol": s} for s in ("002156.SZ", "600171.SH")]
        plan = plan_paper_orders(picks, {}, QUOTES, equity=1_000_000, cash=100_000, max_new_per_pass=2)
        spent = sum(item["notional"] for item in plan["buys"])
        self.assertLessEqual(spent, 100_000)
        self.assertGreaterEqual(plan["cash_after_plan"], 0)

    def test_a_stopped_out_holding_is_sold_with_only_its_sellable_quantity(self):
        # T+1: the rest is not sellable today, and claiming otherwise would
        # make the simulation more permissive than the market.
        positions = {"002156.SZ": {"sellable_quantity": 500, "quantity": 1600, "average_cost": 100.0}}
        plan = plan_paper_orders([], positions, {"002156.SZ": {"price": 90.0}},
                                 equity=1_000_000, cash=0)
        self.assertEqual(plan["sells"][0]["quantity"], 500)
        self.assertEqual(plan["sells"][0]["reason"], "stop_loss")

    def test_a_holding_bought_today_is_not_sold_today(self):
        positions = {"002156.SZ": {"sellable_quantity": 0, "quantity": 1600, "average_cost": 100.0}}
        plan = plan_paper_orders([], positions, {"002156.SZ": {"price": 90.0}},
                                 equity=1_000_000, cash=0)
        self.assertEqual(plan["sells"], [])

    def test_selling_frees_a_slot_within_the_same_pass(self):
        positions = {f"{600000 + i}.SH": {"sellable_quantity": 100, "average_cost": 100.0} for i in range(8)}
        quotes = {**QUOTES, **{f"{600000 + i}.SH": {"price": 90.0} for i in range(8)}}
        plan = plan_paper_orders([CONFIRMED], positions, quotes, equity=1_000_000, cash=1_000_000)
        self.assertEqual(len(plan["sells"]), 8)
        self.assertEqual(len(plan["buys"]), 1)

    def test_a_candidate_with_no_price_is_skipped_rather_than_guessed(self):
        plan = plan_paper_orders([{**CONFIRMED, "symbol": "999999.SZ"}], {}, QUOTES,
                                 equity=1_000_000, cash=1_000_000)
        self.assertEqual(plan["skipped"][0]["reason"], "no_usable_price")

    def test_the_plan_states_it_is_a_simulation(self):
        plan = plan_paper_orders([], {}, {}, equity=0, cash=0)
        self.assertIn("no broker client", plan["boundary"])


if __name__ == "__main__":
    unittest.main()
