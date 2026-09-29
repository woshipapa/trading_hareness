from datetime import date, datetime, timezone, timedelta
from decimal import Decimal
import unittest

from app.paper_execution import estimate_cost, paper_tradability, round_lot, triple_barrier_label
from app.ashare_reality import price_limit_state
from app.paper_execution_service import configure_paper_account
from app.strategy_ablation import ablation_scores
from app.paper_portfolio import paper_risk_gate
from app.datasources.catalog import NON_SECTOR_GROUPS, NON_SECTOR_LABEL_PATTERN
from app.strategy_contracts import EvidenceRef, SignalSpec, contract_payload


class PaperExecutionTests(unittest.TestCase):
    def test_analyst_shadow_is_bounded_and_live_zero(self):
        scores = ablation_scores(market_signal=0.4, analyst_signal=-0.4,
                                 has_analyst_evidence=True, applied_weight=0.0)
        self.assertLess(scores["analyst_shadow_score"], scores["market_only_score"])
        self.assertEqual(scores["applied_score"], scores["market_only_score"])
        self.assertEqual(scores["shadow_weight"], 0.1)

    def test_filled_shared_paper_ledger_cannot_reset_cash(self):
        class FilledConnection:
            def execute(self, sql, params=None):
                class Result:
                    def __init__(self, row):
                        self.row = row

                    def fetchone(self):
                        return self.row
                if "SELECT cash FROM quant.paper_accounts" in sql:
                    return Result({"cash": Decimal("10000")})
                if "SELECT EXISTS(SELECT 1 FROM quant.paper_order_fills)" in sql:
                    return Result({"exists": True})
                raise AssertionError(f"unexpected SQL: {sql}")

        with self.assertRaisesRegex(ValueError, "filled activity"):
            configure_paper_account(FilledConnection(), account_key="default",
                                    initial_cash=Decimal("1000"), configured_by="test")
    def test_round_lot_and_t_plus_one_are_conservative(self):
        self.assertEqual(round_lot(249), 200)
        result = paper_tradability(side="sell", requested_quantity=100, quote={"price": 10},
                                   position={"sellable_quantity": 0})
        self.assertFalse(result.allowed)
        self.assertIn("t_plus_one_or_insufficient_sellable_quantity", result.reasons)

    def test_limit_and_cost_model(self):
        result = paper_tradability(side="buy", requested_quantity=100,
                                   quote={"pct_change": 10.0, "price": 10})
        self.assertFalse(result.allowed)
        costs = estimate_cost(side="sell", quantity=100, price=Decimal("10"))
        self.assertEqual(costs["notional"], Decimal("1000"))
        self.assertGreater(costs["total_cost"], Decimal("5"))

    def test_board_and_st_limit_fallback_uses_the_correct_price_band(self):
        self.assertTrue(paper_tradability(
            side="buy", requested_quantity=100, symbol="300001.SZ", quote={"pct_change": 20.0, "price": 10},
        ).allowed is False)
        self.assertTrue(paper_tradability(
            side="buy", requested_quantity=100, symbol="300001.SZ", quote={"pct_change": 10.0, "price": 10},
        ).allowed)
        self.assertFalse(paper_tradability(
            side="buy", requested_quantity=100, symbol="830001.BJ", quote={"pct_change": 30.0, "price": 10},
        ).allowed)
        # Main-board ST: 5% band before 2026-07-06, 10% from that session on.
        self.assertFalse(paper_tradability(
            side="buy", requested_quantity=100, symbol="600001.SH",
            quote={"pct_change": 5.0, "price": 10, "is_st": True, "price_trade_date": "20260703"},
        ).allowed)
        self.assertTrue(paper_tradability(
            side="buy", requested_quantity=100, symbol="600001.SH",
            quote={"pct_change": 5.0, "price": 10, "is_st": True, "price_trade_date": "20260925"},
        ).allowed)
        self.assertFalse(paper_tradability(
            side="buy", requested_quantity=100, symbol="600001.SH",
            quote={"pct_change": 10.0, "price": 10, "is_st": True, "price_trade_date": "20260925"},
        ).allowed)

    def test_exact_limit_price_precedes_percent_fallback(self):
        state = price_limit_state(symbol="300001.SZ", quote={"price": 12, "pct_change": 10, "limit_up": 12, "limit_down": 8})
        self.assertTrue(state["at_limit_up"])
        self.assertFalse(state["at_limit_down"])

    def test_triple_barrier_is_point_in_time_and_matures(self):
        start = datetime(2026, 8, 14, 1, 30, tzinfo=timezone.utc)
        spec = type("Spec", (), {"upper_return": 0.03, "lower_return": -0.02, "max_horizon_minutes": 60, "cost_bps": 18})
        path = [{"observed_at": start + timedelta(minutes=5), "close": 103}]
        labeled = triple_barrier_label(path, entry_price=100, entry_at=start, spec=spec)
        self.assertEqual(labeled["label"], "upper")
        self.assertEqual(labeled["cost_bps"], 18.0)
        self.assertLess(labeled["return"], labeled["gross_return"])

    def test_contract_payload_is_json_safe(self):
        observed = datetime(2026, 8, 14, tzinfo=timezone.utc)
        signal = SignalSpec("watchlist-confirmation", "v1", "watch", "000001.SZ", 1, observed,
                            evidence=(EvidenceRef("tencent", observed_at=observed),))
        payload = contract_payload(signal)
        self.assertEqual(payload["symbol"], "000001.SZ")
        self.assertEqual(payload["evidence"][0]["observed_at"], observed.isoformat())

    def test_watch_is_not_a_virtual_position(self):
        decision = paper_risk_gate(signal_type="watch", symbol="000001.SZ")
        self.assertTrue(decision.allowed)
        self.assertEqual(decision.target_weight, 0.0)

    def test_sector_concentration_blocks_new_entry(self):
        decision = paper_risk_gate(
            signal_type="entry", symbol="000001.SZ",
            snapshot={"sector_exposure": {"semiconductor": 0.19}},
            candidate_sector_keys=["semiconductor"], max_target_weight=0.05,
            max_sector_exposure=0.20,
        )
        self.assertFalse(decision.allowed)
        self.assertIn("sector_exposure_limit", decision.reasons)
        self.assertIn("paper_sector_exposure_block", decision.risk_flags)

    def test_drawdown_and_daily_loss_block_new_entry(self):
        decision = paper_risk_gate(
            signal_type="entry", symbol="000001.SZ",
            snapshot={"drawdown": -0.10, "daily_return": -0.04},
        )
        self.assertFalse(decision.allowed)
        self.assertIn("portfolio_drawdown_limit", decision.reasons)
        self.assertIn("paper_daily_loss_limit", decision.reasons)

    def test_mark_to_market_splits_sector_exposure(self):
        from app.paper_portfolio import mark_to_market
        snapshot = mark_to_market(
            positions=[{"symbol": "000001.SZ", "quantity": 100, "average_cost": 10,
                        "sector_keys": ["bank", "large_cap"]}],
            quotes={"000001.SZ": {"price": 10}}, cash=0,
        )
        self.assertAlmostEqual(snapshot["sector_exposure"]["bank"], 0.5)
        self.assertAlmostEqual(snapshot["sector_exposure"]["large_cap"], 0.5)

    def test_portfolio_sector_membership_is_point_in_time(self):
        from app.paper_portfolio import persist_portfolio_snapshot

        class Connection:
            def __init__(self):
                self.calls = []

            def execute(self, sql, params=None):
                self.calls.append((sql, params))
                class Result:
                    def fetchall(self):
                        return []

                    def fetchone(self):
                        return None
                return Result()

        connection = Connection()
        persist_portfolio_snapshot(
            connection, as_of=datetime(2026, 8, 14, 0, 30, tzinfo=timezone.utc),
            quotes={}, cash=1000,
        )
        membership_sql, params = connection.calls[0]
        self.assertIn("effective_from<=%s", membership_sql)
        day = datetime(2026, 8, 14).date()
        # Held exposure buckets are read exactly as a candidate's sectors are.
        self.assertEqual(params, (day, day, day, ["ths_concept_flow", "ths_index_n", "ths_industry"],
                                  list(NON_SECTOR_GROUPS), NON_SECTOR_LABEL_PATTERN))
        self.assertIn("NOT (m.sector_key = ANY(%s))", membership_sql)
        self.assertEqual(membership_sql.count("%s"), len(params))


class PaperPortfolioRiskStateTests(unittest.TestCase):
    def test_a_holding_without_a_quote_is_marked_at_cost_not_zero(self):
        from app.paper_portfolio import mark_to_market
        snapshot = mark_to_market(positions=[{"symbol": "600000.SH", "quantity": 1000, "average_cost": 10}],
                                  quotes={}, cash=90_000)
        self.assertEqual(snapshot["equity"], 100_000)

    def test_drawdown_is_measured_from_the_high_water_mark(self):
        from app.paper_portfolio import mark_to_market, paper_risk_gate
        # Equity peaked at 110k; the prior minute was 92k and now 91k.  One
        # minute's move is ~1%, but the book is 17% under its peak.
        snapshot = mark_to_market(positions=[], quotes={}, cash=91_000, previous_equity=92_000, peak_equity=110_000)
        self.assertAlmostEqual(snapshot["drawdown"], 91_000 / 110_000 - 1, places=6)
        self.assertIn("portfolio_drawdown_limit", paper_risk_gate(signal_type="entry", symbol="600000.SH", snapshot=snapshot).reasons)

    def test_daily_loss_uses_the_last_equity_before_this_session(self):
        from app.paper_portfolio import persist_portfolio_snapshot

        class Connection:
            def __init__(self):
                self.calls = []

            def execute(self, sql, params=None):
                self.calls.append((sql, params))

                class Result:
                    def fetchall(self):
                        return []

                    def fetchone(self):
                        return {"peak_equity": 100_000, "previous_close_equity": 100_000} if "max(equity)" in sql else None
                return Result()

        connection = Connection()
        snapshot = persist_portfolio_snapshot(
            connection, as_of=datetime(2026, 9, 25, 2, 0, tzinfo=timezone.utc), quotes={}, cash=96_000,
        )
        self.assertAlmostEqual(snapshot["daily_return"], -0.04, places=6)
        history_sql, history_params = next(call for call in connection.calls if "max(equity)" in call[0])
        # Session start is Shanghai midnight of the snapshot's exchange date.
        self.assertEqual(history_params[0], datetime(2026, 9, 24, 16, 0, tzinfo=timezone.utc))


class RoundTripCostPercentTests(unittest.TestCase):
    """Research settles in percentages and cannot call the notional estimator.

    The percentage form has to stay derived from the same constants: a second
    rate table would drift from the paper-trading one, and every net figure in
    the scorecards would then be judged against a cost nobody trades at.
    """

    def test_it_is_one_buy_plus_one_sell_from_the_shared_constants(self):
        from app.ashare_reality import (
            DEFAULT_COMMISSION_RATE, DEFAULT_SLIPPAGE_BPS, DEFAULT_STAMP_TAX_RATE,
            DEFAULT_TRANSFER_FEE_RATE, round_trip_cost_pct,
        )
        slippage = DEFAULT_SLIPPAGE_BPS / Decimal("10000")
        expected = ((DEFAULT_COMMISSION_RATE + DEFAULT_TRANSFER_FEE_RATE + slippage)
                    + (DEFAULT_COMMISSION_RATE + DEFAULT_TRANSFER_FEE_RATE + DEFAULT_STAMP_TAX_RATE + slippage)) * Decimal("100")
        self.assertEqual(round_trip_cost_pct(), expected)

    def test_stamp_tax_is_charged_once_on_the_sell_leg_only(self):
        from app.ashare_reality import DEFAULT_STAMP_TAX_RATE, round_trip_cost_pct
        without_stamp = round_trip_cost_pct(stamp_tax_rate=Decimal("0"))
        self.assertEqual(round_trip_cost_pct() - without_stamp,
                         DEFAULT_STAMP_TAX_RATE * Decimal("100"))

    def test_it_agrees_with_the_notional_estimator_above_the_commission_floor(self):
        from app.ashare_reality import estimate_trade_cost, round_trip_cost_pct
        # 10,000 shares at 50 is far above the 5 yuan floor, so the two forms
        # must agree; below the floor they deliberately do not, which is why
        # the percentage form documents that it understates small positions.
        quantity, price = 10_000, Decimal("50")
        notional = price * quantity
        buy = estimate_trade_cost(side="buy", quantity=quantity, price=price)
        sell = estimate_trade_cost(side="sell", quantity=quantity, price=price)
        combined = (buy["total_cost"] + sell["total_cost"]) / notional * Decimal("100")
        self.assertAlmostEqual(float(combined), float(round_trip_cost_pct()), places=9)

    def test_the_current_statutory_rates_are_in_force(self):
        from app.ashare_reality import DEFAULT_STAMP_TAX_RATE, DEFAULT_TRANSFER_FEE_RATE, estimate_trade_cost
        # Stamp duty was halved to 0.05% on 2023-08-28; transfer fee is 0.001%.
        self.assertEqual(DEFAULT_STAMP_TAX_RATE, Decimal("0.0005"))
        self.assertEqual(DEFAULT_TRANSFER_FEE_RATE, Decimal("0.00001"))
        sell = estimate_trade_cost(side="sell", quantity=10_000, price=Decimal("10"))
        self.assertEqual(sell["stamp_tax"], Decimal("50.0000"))
        self.assertEqual(sell["transfer_fee"], Decimal("1.00000"))

    def test_the_round_trip_is_material_against_the_edges_being_measured(self):
        from app.ashare_reality import round_trip_cost_pct
        # 2026-08-27: supplement_rotation settled at +0.36% gross. If the
        # round trip ever falls below that the guard this protects is gone.
        self.assertGreater(float(round_trip_cost_pct()), 0.2)


if __name__ == "__main__":
    unittest.main()


class PaperAutoExitTests(unittest.TestCase):
    def _plan(self, positions, quotes, candidates=(), session=date(2026, 9, 25)):
        from app.paper_auto_execution import plan_paper_orders
        return plan_paper_orders(list(candidates), positions, quotes, equity=1_000_000, cash=500_000, session_date=session)

    def test_a_held_name_with_no_candidate_this_pass_is_stopped_out(self):
        positions = {"600000.SH": {"symbol": "600000.SH", "quantity": 1000, "sellable_quantity": 1000,
                                   "average_cost": 10, "buy_date": date(2026, 9, 24)}}
        plan = self._plan(positions, {"600000.SH": {"price": 8.5}})
        self.assertEqual([(sell["symbol"], sell["reason"]) for sell in plan["sells"]],
                         [("600000.SH", "fallback_stop_loss")])

    def test_the_entry_stop_is_kept_when_the_strategy_is_silent(self):
        positions = {"600000.SH": {"symbol": "600000.SH", "quantity": 1000, "sellable_quantity": 1000,
                                   "average_cost": 10, "buy_date": date(2026, 9, 24), "entry_stop_loss_pct": 15}}
        # Down 12%: inside the strategy's own 15% stop, so the 8% fallback does not apply.
        self.assertEqual(self._plan(positions, {"600000.SH": {"price": 8.8}})["sells"], [])
        self.assertEqual(self._plan(positions, {"600000.SH": {"price": 8.4}})["sells"][0]["reason"], "strategy_stop_loss")

    def test_a_stale_holding_leaves_after_the_fallback_holding_period(self):
        positions = {"600000.SH": {"symbol": "600000.SH", "quantity": 1000, "sellable_quantity": 1000,
                                   "average_cost": 10, "buy_date": date(2026, 9, 18)}}
        plan = self._plan(positions, {"600000.SH": {"price": 10.2}})
        self.assertEqual(plan["sells"][0]["reason"], "fallback_max_holding_period")

    def test_a_bought_order_carries_the_drill_delivery_key(self):
        candidate = {"symbol": "600000.SH", "direction": "inflow", "delivery_key": "600000.SH:ths:881101:inflow"}
        plan = self._plan({}, {"600000.SH": {"price": 10}}, [candidate])
        self.assertEqual(plan["buys"][0]["delivery_key"], "600000.SH:ths:881101:inflow")


class PaperFillQuoteAgeTests(unittest.TestCase):
    def _accept(self, quote_age_seconds):
        import uuid
        from decimal import Decimal
        from app.paper_execution_service import accept_paper_decision

        accepted_at = datetime(2026, 9, 25, 2, 0, tzinfo=timezone.utc)
        decision_id = uuid.uuid4()
        quote = {"source_name": "tencent_free", "observed_at": accepted_at - timedelta(seconds=quote_age_seconds),
                 "price": Decimal("10"), "pct_change": 1.0, "raw": {}}
        calls = []

        class Connection:
            def execute(self, sql, params=None):
                calls.append(sql)

                class Result:
                    rowcount = 1

                    def fetchone(self):
                        if "FROM quant.paper_decisions" in sql:
                            return {"decision_id": decision_id, "symbol": "600000.SH", "direction": 1, "status": "proposed",
                                    "decision_at": accepted_at, "evidence": {}, "risk_flags": []}
                        if "FROM quant.intraday_quote_observations" in sql:
                            return quote
                        if "FROM quant.paper_accounts" in sql:
                            return {"account_key": "default", "cash": Decimal("1000000")}
                        if "INSERT INTO quant.paper_orders" in sql:
                            return {"order_id": uuid.uuid4()}
                        return None
                return Result()

        return accept_paper_decision(Connection(), decision_id=decision_id, quantity=100, accepted_at=accepted_at)

    def test_a_fresh_quote_fills(self):
        self.assertEqual(self._accept(20)["status"], "filled")

    def test_a_stale_quote_does_not_fill(self):
        result = self._accept(1800)
        self.assertEqual(result["status"], "non_fill")
        self.assertIn("local_quote_stale", result["reason_codes"])
