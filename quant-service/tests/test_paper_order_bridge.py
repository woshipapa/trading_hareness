import unittest
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

from app.paper_order_bridge import (
    STAGE, decision_payload, execute_plan, place_order, signal_key,
)

NOW = datetime(2026, 9, 19, 1, 35, tzinfo=timezone.utc)
BUY = {"symbol": "301583.SZ", "side": "buy", "quantity": 600, "price": 142.56,
       "strategy_key": "xiaojie_leader_flow", "mode": "潜龙出海_swing",
       "entry_fraction": 0.09, "sizing_source": "strategy", "stop_loss_pct": 8.0,
       "risk_flags": [], "reason": "board_flow_drill_confirmed"}
SELL = {"symbol": "002156.SZ", "side": "sell", "quantity": 500, "price": 60.0,
        "strategy_key": "xiaojie_leader_flow", "reason": "strategy_exit:exit",
        "exit_codes": ["ma5_break_unrecovered"]}


class _Connection:
    def __init__(self, *, decision_found=True):
        self.statements = []
        self.decision_found = decision_found

    def execute(self, statement, values=None):
        self.statements.append((" ".join(statement.split()), values))
        found = {"decision_id": uuid.uuid4()} if self.decision_found else None
        return SimpleNamespace(fetchone=lambda: found, fetchall=lambda: [], rowcount=1)


def _accept(recorded):
    def accept(connection, *, decision_id, quantity, accepted_at, account_key):
        recorded.append({"decision_id": decision_id, "quantity": quantity,
                         "account_key": account_key})
        return {"status": "filled", "filled_quantity": quantity}
    return accept


class SignalKeyTests(unittest.TestCase):
    def test_a_key_is_one_name_one_strategy_one_side(self):
        self.assertEqual(signal_key(BUY), "301583.SZ:xiaojie_leader_flow:buy")

    def test_the_opposite_side_is_a_different_key(self):
        self.assertNotEqual(signal_key(BUY), signal_key({**BUY, "side": "sell"}))


class DecisionPayloadTests(unittest.TestCase):
    def test_a_buy_is_a_positive_direction(self):
        self.assertEqual(decision_payload(BUY, NOW)["direction"], 1)

    def test_a_sell_is_a_negative_direction(self):
        self.assertEqual(decision_payload(SELL, NOW)["direction"], -1)

    def test_the_strategy_that_produced_it_is_recorded(self):
        # The ledger exists to measure the strategies, so every order has to
        # name the one that asked for it.
        payload = decision_payload(BUY, NOW)
        self.assertEqual(payload["strategy_key"], "xiaojie_leader_flow")
        self.assertEqual(payload["evidence"]["mode"], "潜龙出海_swing")
        self.assertEqual(payload["evidence"]["sizing_source"], "strategy")

    def test_every_order_is_flagged_as_simulated_and_automatic(self):
        self.assertIn("paper_only", decision_payload(BUY, NOW)["risk_flags"])
        self.assertIn("auto_executed", decision_payload(BUY, NOW)["risk_flags"])


class PlaceOrderTests(unittest.TestCase):
    def test_an_order_is_anchored_to_a_research_staged_signal_event(self):
        # Research staging is what stops a decision-path consumer selecting
        # these alongside watchlist alerts.
        connection, accepted = _Connection(), []
        place_order(connection, BUY, NOW, persist_decision=lambda *_a: True,
                    accept_decision=_accept(accepted), json_safe=lambda value: value)
        insert = next(s for s, _v in connection.statements if "intraday_signal_events" in s)
        self.assertIn("stage", insert)
        values = next(v for s, v in connection.statements if "intraday_signal_events" in s)
        self.assertEqual(values[-1], STAGE)
        self.assertNotIsInstance(values[5], dict)
        self.assertNotIsInstance(values[6], dict)

    def test_the_ledgers_own_acceptance_places_the_order(self):
        connection, accepted = _Connection(), []
        result = place_order(connection, BUY, NOW, persist_decision=lambda *_a: True,
                             accept_decision=_accept(accepted), json_safe=lambda value: value)
        self.assertEqual(result["status"], "submitted")
        self.assertEqual(accepted[0]["quantity"], 600)
        self.assertEqual(result["ledger"]["status"], "filled")

    def test_a_duplicate_decision_is_not_accepted_twice(self):
        connection, accepted = _Connection(), []
        result = place_order(connection, BUY, NOW, persist_decision=lambda *_a: False,
                             accept_decision=_accept(accepted), json_safe=lambda value: value)
        self.assertEqual(result["status"], "duplicate")
        self.assertEqual(accepted, [])

    def test_a_decision_that_did_not_store_is_not_accepted(self):
        connection, accepted = _Connection(decision_found=False), []
        result = place_order(connection, BUY, NOW, persist_decision=lambda *_a: True,
                             accept_decision=_accept(accepted), json_safe=lambda value: value)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(accepted, [])


class ExecutePlanTests(unittest.TestCase):
    def test_sells_are_placed_before_buys(self):
        # Cash freed by an exit has to exist before the entry that spends it.
        connection, accepted = _Connection(), []
        order_log = []

        def accept(connection_, *, decision_id, quantity, accepted_at, account_key):
            order_log.append(quantity)
            return {"status": "filled"}

        execute_plan(connection, {"buys": [BUY], "sells": [SELL]}, NOW,
                     persist_decision=lambda *_a: True, accept_decision=accept,
                     json_safe=lambda value: value)
        self.assertEqual(order_log, [500, 600])

    def test_one_refusal_does_not_end_the_pass(self):
        # A name the ledger refuses says nothing about the next, and abandoning
        # the pass would silently drop exits that were already due.
        calls = {"n": 0}

        def accept(connection_, *, decision_id, quantity, accepted_at, account_key):
            calls["n"] += 1
            if calls["n"] == 1:
                raise ValueError("limit up: not tradable")
            return {"status": "filled"}

        result = execute_plan(_Connection(), {"buys": [BUY], "sells": [SELL]}, NOW,
                              persist_decision=lambda *_a: True, accept_decision=accept,
                              json_safe=lambda value: value)
        self.assertEqual(result["failed"], 1)
        self.assertEqual(result["submitted"], 1)

    def test_an_empty_plan_places_nothing(self):
        result = execute_plan(_Connection(), {"buys": [], "sells": []}, NOW,
                              persist_decision=lambda *_a: True,
                              accept_decision=lambda *_a, **_k: {}, json_safe=lambda v: v)
        self.assertEqual(result["placed"], [])
        self.assertIn("no broker client", result["boundary"])


if __name__ == "__main__":
    unittest.main()
