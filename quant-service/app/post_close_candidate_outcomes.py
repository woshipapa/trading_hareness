"""Outcome settlement for post-close/leader-rotation candidate lines.

``post_close_strategy_candidates`` and ``ten_day_leader_rotation_candidates``
are both post-close, run_id/symbol-keyed shadow candidate lines with no
existing outcome linkage: nothing ever measured what happened after either
strategy proposed a symbol, so neither could ever accumulate the evidence its
own promotion gate requires. Both tables are structurally identical for
settlement purposes, so one parametrized function settles either.

Every candidate is treated as a long/watch idea (neither table carries an
explicit direction). Settlement is the shared T+1 rule in t1_settlement.py:
entry at the next session's open (a locked limit-up open or a suspended entry
is never settled), exit at the horizon close rolled past limit-down or
suspension, adjusted prices, net of costs, against the equal-weight market.

The forward horizon is not documented by either upstream strategy. 10
trading days is used because it already is this codebase's dominant
convention for a comparable shadow evaluation window (HORIZON_DAYS in
watchlist_main_wave.py and watchlist_countertrend_rebound.py, and the
explicit ``return_horizon_sessions: 10`` ten_day_leader_ranking.py already
states for its own trailing measurement) - not a claim that either strategy
was designed around exactly 10 days.
"""

from __future__ import annotations

from datetime import date
from typing import Any, NamedTuple

from .t1_settlement import SETTLEMENT_VERSION
from .t1_settlement_repository import (
    EqualWeightMarket, settle_idea, settlement_columns, settlement_update_sql, settlement_values,
)


class CandidateOutcomeTarget(NamedTuple):
    candidates_table: str
    runs_table: str
    outcomes_table: str
    horizon_days: int


POST_CLOSE_STRATEGY_CANDIDATES = CandidateOutcomeTarget(
    "post_close_strategy_candidates", "post_close_strategy_runs", "post_close_strategy_candidate_outcomes", 10,
)
TEN_DAY_LEADER_ROTATION_CANDIDATES = CandidateOutcomeTarget(
    "ten_day_leader_rotation_candidates", "ten_day_leader_rotation_runs", "ten_day_leader_rotation_candidate_outcomes", 10,
)


def settle_candidate_outcomes(connection: Any, as_of_date: date, target: CandidateOutcomeTarget) -> int:
    """Settle every candidate not yet settled under the current T+1 rule.

    ``target``'s table names are internal, hardcoded module constants, never
    request input, so building the query with an f-string is safe here.
    """
    candidates = [dict(row) for row in connection.execute(
        f"""SELECT c.run_id,c.symbol,r.as_of_date run_date
              FROM quant.{target.candidates_table} c
              JOIN quant.{target.runs_table} r ON r.run_id=c.run_id
             WHERE r.as_of_date<%s
               AND NOT EXISTS (
                   SELECT 1 FROM quant.{target.outcomes_table} settled
                    WHERE settled.run_id=c.run_id AND settled.symbol=c.symbol
                      AND settled.settlement_version=%s)""",
        (as_of_date, SETTLEMENT_VERSION),
    ).fetchall()]
    if not candidates:
        return 0
    market = EqualWeightMarket(connection, min(row["run_date"] for row in candidates), as_of_date)
    columns = ["run_id", "symbol", "horizon_days", *settlement_columns("entry_price", "exit_price")]
    settled = 0
    for row in candidates:
        result = settle_idea(connection, symbol=row["symbol"], signal_date=row["run_date"], as_of_date=as_of_date,
                             direction=1, horizon_sessions=target.horizon_days, market=market)
        if result["status"] != "settled":
            continue
        connection.execute(
            f"""INSERT INTO quant.{target.outcomes_table}({",".join(columns)})
                VALUES({",".join(["%s"] * len(columns))})
                ON CONFLICT(run_id, symbol) DO UPDATE SET horizon_days=EXCLUDED.horizon_days,
                  {settlement_update_sql("entry_price", "exit_price", keys=("run_id", "symbol"))}""",
            (row["run_id"], row["symbol"], target.horizon_days, *settlement_values(result)),
        )
        settled += 1
    return settled


def settle_post_close_and_leader_rotation_outcomes(connection: Any, as_of_date: date) -> dict[str, int]:
    return {
        "post_close_strategy_candidate_outcomes": settle_candidate_outcomes(connection, as_of_date, POST_CLOSE_STRATEGY_CANDIDATES),
        "ten_day_leader_rotation_candidate_outcomes": settle_candidate_outcomes(connection, as_of_date, TEN_DAY_LEADER_ROTATION_CANDIDATES),
    }


__all__ = [
    "POST_CLOSE_STRATEGY_CANDIDATES", "TEN_DAY_LEADER_ROTATION_CANDIDATES", "CandidateOutcomeTarget",
    "settle_candidate_outcomes", "settle_post_close_and_leader_rotation_outcomes",
]
