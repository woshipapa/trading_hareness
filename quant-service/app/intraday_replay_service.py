"""Re-run recorded intraday rule inputs: the exact replay and the entry-timing challengers.

Both read only the rule-input snapshots the scan froze, so neither touches a
provider or current market state.  Moved out of main.py (docs/decisions/0008).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, time
from typing import Any
from zoneinfo import ZoneInfo

from .intraday_rule_input_replay_runner import run_recorded_rule_input_replay
from .intraday_signal_rules import signal_rules as pure_signal_rules
from .live_policy import live_policy_gate
from .numeric_utils import intraday_number
from .paper_portfolio import paper_risk_gate
from .strategy_timing_challengers import run_challenger_backtest

SHANGHAI = ZoneInfo("Asia/Shanghai")


@dataclass(frozen=True)
class IntradayReplayDependencies:
    database: Any
    model_version: str
    #: The service's own rule entry point, which the live scan also calls.
    signal_rules: Callable[..., list[dict[str, Any]]]
    upside_assessment: Callable[..., Any]


def replay_recorded_rule_inputs(payload: Any, deps: IntradayReplayDependencies) -> dict[str, Any]:
    def evaluate(inputs: dict[str, Any]) -> list[dict[str, Any]]:
        return deps.signal_rules(
            inputs["watch"], inputs["quote"], inputs["previous_quote"], inputs["daily_factors"],
            inputs["minute_features"], inputs["peer_context"],
        )

    def evaluate_policy(signal: dict[str, Any], inputs: dict[str, Any]) -> dict[str, Any]:
        """Replay the same pure risk/policy gate from snapshot-local inputs.

        V1 snapshots never call this function because they did not capture the
        required point-in-time market and portfolio values.  The generic
        replay runner labels them core-rule-only instead of reading current
        database state.
        """
        portfolio_context = dict(inputs.get("portfolio_context") or {})
        portfolio_gate = paper_risk_gate(
            signal_type=str(signal.get("signal_type") or "watch"),
            symbol=str(inputs["watch"]["symbol"]),
            position=dict(portfolio_context.get("position") or {}),
            snapshot=dict(portfolio_context.get("snapshot") or {}),
            candidate_sector_keys=list(portfolio_context.get("candidate_sector_keys") or ()),
        )
        portfolio_risk = {
            "allowed": portfolio_gate.allowed, "target_weight": portfolio_gate.target_weight,
            "reasons": list(portfolio_gate.reasons), "risk_flags": list(portfolio_gate.risk_flags),
        }
        return live_policy_gate(
            signal, inputs["watch"], inputs["quote"], inputs["daily_factors"],
            dict(inputs.get("market_context") or {}), dict(inputs.get("fast_confirmation") or {}),
            portfolio_risk,
        )

    with deps.database.transaction() as connection:
        return run_recorded_rule_input_replay(
            connection, as_of_date=payload.as_of_date, max_rows=payload.max_rows,
            model_version=deps.model_version, evaluate=evaluate, evaluate_policy=evaluate_policy,
        )


def run_entry_timing_challengers(payload: Any, deps: IntradayReplayDependencies) -> dict[str, Any]:
    def evaluate_variant(inputs: dict[str, Any], overrides: dict[str, Any]) -> list[dict[str, Any]]:
        observed_at = (inputs.get("quote") or {}).get("_scan_observed_at")
        opening_gap_window = (
            isinstance(observed_at, datetime)
            and time(9, 30) <= observed_at.astimezone(SHANGHAI).time() < time(9, 40)
        )
        return pure_signal_rules(
            inputs["watch"], inputs["quote"], inputs["previous_quote"], inputs["daily_factors"],
            inputs["minute_features"], inputs["peer_context"],
            number=intraday_number, upside_assessment_fn=deps.upside_assessment,
            model_version=deps.model_version, opening_gap_window=opening_gap_window,
            **overrides,
        )

    with deps.database.transaction() as connection:
        as_of_date = payload.as_of_date
        if as_of_date is None:
            row = connection.execute(
                """SELECT max((observed_at AT TIME ZONE 'Asia/Shanghai')::date) AS d
                     FROM quant.intraday_rule_input_snapshots WHERE model_version=%s""",
                (deps.model_version,),
            ).fetchone()
            as_of_date = row["d"] if row else None
        if as_of_date is None:
            return {"status": "blocked", "reason": "no recorded rule-input snapshots for this model version"}
        return run_challenger_backtest(
            connection, as_of_date, model_version=deps.model_version,
            evaluate_variant=evaluate_variant, max_rows=payload.max_rows,
        )


__all__ = ["IntradayReplayDependencies", "replay_recorded_rule_inputs", "run_entry_timing_challengers"]
