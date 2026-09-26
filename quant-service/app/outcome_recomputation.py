"""Local-only daily outcome recomputation for analyst claims and recommendations."""

from __future__ import annotations

from typing import Any, Callable

from .t1_settlement import SETTLEMENT_VERSION
from .t1_settlement_repository import EqualWeightMarket, settle_idea, settlement_columns, settlement_update_sql, settlement_values


def recompute(
    as_of_date: Any = None,
    *,
    cn_today: Callable[[], Any],
    db: Any,
    recompute_intraday_signal_outcomes: Callable[[Any], dict[str, Any]],
    settle_post_close_and_leader_rotation_outcomes: Callable[[Any, Any], dict[str, int]] | None = None,
    settle_ledger_outcomes: Callable[[Any, Any], int] | None = None,
    evaluate_trial_families: Callable[[Any, Any], dict[str, int]] | None = None,
) -> dict[str, Any]:
    """Close only outcomes whose already-persisted future bars are observable."""
    as_of_date = as_of_date or cn_today()
    with db.transaction() as connection:
        claims = [dict(row) for row in connection.execute(
            r"""SELECT c.claim_id,c.subject_key symbol,c.horizon_days,c.direction,
                       (c.available_at AT TIME ZONE 'Asia/Shanghai')::date signal_date
                  FROM quant.analyst_claims c
                 WHERE c.scope='stock' AND c.subject_key ~ '^\d{6}\.(SH|SZ|BJ)$' AND c.direction<>0
                   AND (c.available_at AT TIME ZONE 'Asia/Shanghai')::date<%s
                   AND NOT EXISTS (
                       SELECT 1 FROM quant.outcomes settled
                        WHERE settled.claim_id=c.claim_id AND settled.horizon_days=c.horizon_days
                          AND settled.settlement_version=%s)""",
            (as_of_date, SETTLEMENT_VERSION),
        ).fetchall()]
        recommendations = [dict(row) for row in connection.execute(
            """SELECT r.run_id,r.as_of_date run_date,x.symbol,x.direction,x.horizon_days
                 FROM quant.recommendation_runs r JOIN quant.recommendations x ON x.run_id=r.run_id
                WHERE r.as_of_date<%s AND x.direction<>0
                  AND NOT EXISTS (
                      SELECT 1 FROM quant.outcomes settled
                       WHERE settled.recommendation_run_id=r.run_id AND settled.symbol=x.symbol
                         AND settled.horizon_days=x.horizon_days AND settled.settlement_version=%s)""",
            (as_of_date, SETTLEMENT_VERSION),
        ).fetchall()]
        signal_dates = [row["signal_date"] for row in claims] + [row["run_date"] for row in recommendations]
        market = EqualWeightMarket(connection, min(signal_dates), as_of_date) if signal_dates else None
        columns = settlement_columns("entry_close", "exit_close")
        placeholders = ",".join(["%s"] * (len(columns) + 3))
        rows: list[dict[str, Any]] = []
        for claim in claims:
            # Entry is the first session after the claim became available to a
            # strategy; the T+1 rule decides the exit (t1_settlement.py).
            result = settle_idea(connection, symbol=claim["symbol"], signal_date=claim["signal_date"],
                                 as_of_date=as_of_date, direction=int(claim["direction"]),
                                 horizon_sessions=int(claim["horizon_days"]), market=market)
            if result["status"] != "settled":
                continue
            connection.execute(
                f"""INSERT INTO quant.outcomes(claim_id,symbol,horizon_days,{",".join(columns)})
                    VALUES({placeholders})
                    ON CONFLICT(claim_id,symbol,entry_date,horizon_days) DO UPDATE SET
                      {settlement_update_sql("entry_close", "exit_close", keys=("entry_date",))}""",
                (claim["claim_id"], claim["symbol"], claim["horizon_days"], *settlement_values(result)),
            )
            rows.append(claim)
        recommendation_outcomes = 0
        for recommendation in recommendations:
            result = settle_idea(connection, symbol=recommendation["symbol"], signal_date=recommendation["run_date"],
                                 as_of_date=as_of_date, direction=int(recommendation["direction"]),
                                 horizon_sessions=int(recommendation["horizon_days"]), market=market)
            if result["status"] != "settled":
                continue
            connection.execute(
                f"""INSERT INTO quant.outcomes(recommendation_run_id,symbol,horizon_days,{",".join(columns)})
                    VALUES({placeholders})
                    ON CONFLICT (recommendation_run_id,symbol,entry_date,horizon_days) WHERE recommendation_run_id IS NOT NULL
                    DO UPDATE SET {settlement_update_sql("entry_close", "exit_close", keys=("entry_date",))}""",
                (recommendation["run_id"], recommendation["symbol"], recommendation["horizon_days"],
                 *settlement_values(result)),
            )
            recommendation_outcomes += 1
        candidate_outcomes = (
            settle_post_close_and_leader_rotation_outcomes(connection, as_of_date)
            if settle_post_close_and_leader_rotation_outcomes is not None else {}
        )
        ledger_outcome_rows = settle_ledger_outcomes(connection, as_of_date) if settle_ledger_outcomes is not None else 0
        # Re-deflate each outcome family once its newly settled rows are in.
        trial_families = evaluate_trial_families(connection, as_of_date) if evaluate_trial_families is not None else {}
    intraday = recompute_intraday_signal_outcomes(as_of_date)
    candidate_outcome_rows = sum(candidate_outcomes.values())
    return {"as_of_date": str(as_of_date),
            "outcomes": len(rows) + recommendation_outcomes + intraday["outcome_rows"] + candidate_outcome_rows + ledger_outcome_rows,
            "claim_outcomes": len(rows), "recommendation_outcomes": recommendation_outcomes,
            "intraday_signal_outcomes": intraday, "candidate_outcomes": candidate_outcomes,
            "ledger_outcomes": ledger_outcome_rows, "research_trial_families": trial_families}


__all__ = ["recompute"]
