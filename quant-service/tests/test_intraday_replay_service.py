"""The rule-input replay and the entry-timing challengers, moved out of main.py (docs/decisions/0008)."""

from __future__ import annotations

import unittest
from contextlib import contextmanager
from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

from app import intraday_replay_service as service
from app.intraday_replay_service import (
    IntradayReplayDependencies, replay_recorded_rule_inputs, run_entry_timing_challengers,
)

INPUTS = {
    "watch": {"symbol": "000001.SZ"}, "previous_quote": {}, "daily_factors": {"ma5": 1},
    "minute_features": {}, "peer_context": {},
    "portfolio_context": {"position": {"qty": 100}, "snapshot": {"cash": 1}, "candidate_sector_keys": ["881101"]},
}


class _Connection:
    def __init__(self, latest):
        self.latest = latest

    def execute(self, _sql, params):
        self.params = params
        return SimpleNamespace(fetchone=lambda: {"d": self.latest})


class _Database:
    def __init__(self, latest=None):
        self.connection = _Connection(latest)

    @contextmanager
    def transaction(self):
        yield self.connection


def deps(database, rule_calls=None):
    def rules(*args):
        (rule_calls if rule_calls is not None else []).append(args)
        return [{"signal_type": "entry"}]

    return IntradayReplayDependencies(database=database, model_version="watchlist-confirmation-v8",
                                      signal_rules=rules, upside_assessment=lambda *_a, **_k: None)


class EntryTimingChallengerTests(unittest.TestCase):
    def test_without_any_snapshot_the_run_is_blocked(self):
        database = _Database(latest=None)
        result = run_entry_timing_challengers(SimpleNamespace(as_of_date=None, max_rows=10), deps(database))
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(database.connection.params, ("watchlist-confirmation-v8",))

    def test_variants_see_the_opening_gap_window_and_the_latest_session(self):
        seen = {}

        def backtest(_connection, as_of_date, *, model_version, evaluate_variant, max_rows):
            seen.update(as_of_date=as_of_date, model_version=model_version, max_rows=max_rows)
            at = lambda h, m: datetime(2026, 10, 9, h - 8, m, tzinfo=timezone.utc)  # noqa: E731 - Shanghai h:m
            evaluate_variant({**INPUTS, "quote": {"_scan_observed_at": at(9, 35)}}, {"min_gap": 1})
            evaluate_variant({**INPUTS, "quote": {"_scan_observed_at": at(10, 5)}}, {})
            return {"status": "completed"}

        windows = []
        with patch.object(service, "run_challenger_backtest", backtest), \
                patch.object(service, "pure_signal_rules",
                             lambda *args, **kwargs: windows.append((kwargs["opening_gap_window"], kwargs.get("min_gap"))) or []):
            result = run_entry_timing_challengers(SimpleNamespace(as_of_date=None, max_rows=5),
                                                  deps(_Database(latest=date(2026, 10, 9))))
        self.assertEqual(result["status"], "completed")
        self.assertEqual(seen, {"as_of_date": date(2026, 10, 9), "model_version": "watchlist-confirmation-v8", "max_rows": 5})
        self.assertEqual(windows, [(True, 1), (False, None)])


class RuleInputReplayTests(unittest.TestCase):
    def test_the_replay_reruns_the_rules_and_the_policy_gate_from_snapshot_inputs(self):
        gates, policies, rule_calls = [], [], []

        def replay(_connection, *, as_of_date, max_rows, model_version, evaluate, evaluate_policy):
            signal = evaluate({**INPUTS, "quote": {"price": 10}})[0]
            return {"policy": evaluate_policy(signal, {**INPUTS, "quote": {"price": 10}}), "as_of_date": as_of_date}

        def risk_gate(**kwargs):
            gates.append(kwargs)
            return SimpleNamespace(allowed=True, target_weight=0.1, reasons=("ok",), risk_flags=())

        def policy(signal, watch, quote, daily, market, fast, portfolio_risk):
            policies.append(portfolio_risk)
            return {"decision": "observe"}

        with patch.object(service, "run_recorded_rule_input_replay", replay), \
                patch.object(service, "paper_risk_gate", risk_gate), patch.object(service, "live_policy_gate", policy):
            result = replay_recorded_rule_inputs(SimpleNamespace(as_of_date=date(2026, 10, 9), max_rows=3),
                                                 deps(_Database(), rule_calls))
        self.assertEqual(result, {"policy": {"decision": "observe"}, "as_of_date": date(2026, 10, 9)})
        self.assertEqual(len(rule_calls[0]), 6, "the six recorded inputs, positionally")
        self.assertEqual(gates[0]["signal_type"], "entry")
        self.assertEqual(gates[0]["candidate_sector_keys"], ["881101"])
        self.assertEqual(policies, [{"allowed": True, "target_weight": 0.1, "reasons": ["ok"], "risk_flags": []}])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
