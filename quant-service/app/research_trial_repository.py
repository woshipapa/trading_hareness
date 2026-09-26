"""Persist research-family evaluations and the outcome families that feed them.

``record_family`` is the only writer of ``quant.research_trials``.  It
evaluates every variant of one family together, because the family's null
(trial count and Sharpe dispersion) and its FDR correction both depend on all
of them - including variants evaluated in earlier runs and not in this one.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Iterable, Mapping

from psycopg.types.json import Json

from .research_trials import bh_q_values, evaluate_returns, family_null, parameters_hash, selection_gate
from .t1_settlement import SETTLEMENT_VERSION


def _latest_by_variant(connection: Any, family: str) -> dict[tuple[str, str], dict[str, Any]]:
    rows = connection.execute(
        """SELECT DISTINCT ON (variant_key, parameters_hash) variant_key,parameters_hash,sharpe,p_value
             FROM quant.research_trials WHERE family=%s
            ORDER BY variant_key,parameters_hash,evaluated_at DESC,sample_end DESC NULLS LAST""",
        (family,),
    ).fetchall()
    return {(str(row["variant_key"]), str(row["parameters_hash"])): dict(row) for row in rows}


def record_family(connection: Any, *, family: str, return_basis: str, source: str,
                  variants: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Evaluate and store one pass over a family's variants.

    Each variant mapping carries ``variant_key``, ``parameters``, ``returns``
    (oldest first) and optionally ``sample_start``/``sample_end``.
    """
    current = []
    for variant in variants:
        parameters = dict(variant.get("parameters") or {})
        current.append({**variant, "parameters": parameters, "parameters_hash": parameters_hash(parameters)})
    if not current:
        return []
    known = _latest_by_variant(connection, family)
    preliminary = {
        (str(item["variant_key"]), item["parameters_hash"]): evaluate_returns(item["returns"], null=family_null([]))
        for item in current
    }
    sharpes = {key: float(row["sharpe"]) for key, row in known.items() if row.get("sharpe") is not None}
    sharpes.update({key: value["sharpe"] for key, value in preliminary.items() if value["sharpe"] is not None})
    # Every distinct configuration the family ever evaluated is a trial, even
    # one whose sample is still too small to carry a Sharpe of its own.
    distinct = set(known) | set(preliminary)
    null = family_null(list(sharpes.values()))
    null["trials"] = max(null["trials"], len(distinct))
    evaluations = {key: evaluate_returns(item["returns"], null=null)
                   for key, item in ((
                       (str(item["variant_key"]), item["parameters_hash"]), item) for item in current)}
    p_values = {key: (float(row["p_value"]) if row.get("p_value") is not None else None) for key, row in known.items()}
    p_values.update({key: value["p_value"] for key, value in evaluations.items()})
    q_values = bh_q_values({f"{key[0]}|{key[1]}": value for key, value in p_values.items()})
    stored = []
    for item in current:
        key = (str(item["variant_key"]), item["parameters_hash"])
        evaluation = {**evaluations[key], "q_value": q_values.get(f"{key[0]}|{key[1]}")}
        gate = selection_gate(evaluation)
        connection.execute(
            """INSERT INTO quant.research_trials(
                   family,variant_key,parameters,parameters_hash,return_basis,sample_start,sample_end,observations,
                   mean_return,sharpe,probabilistic_sharpe,deflated_sharpe,family_trials,family_sharpe_variance,
                   expected_maximum_sharpe,p_value,q_value,selection_gate,source)
               VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT(family,variant_key,parameters_hash,sample_start,sample_end) DO UPDATE SET
                 observations=EXCLUDED.observations,mean_return=EXCLUDED.mean_return,sharpe=EXCLUDED.sharpe,
                 probabilistic_sharpe=EXCLUDED.probabilistic_sharpe,deflated_sharpe=EXCLUDED.deflated_sharpe,
                 family_trials=EXCLUDED.family_trials,family_sharpe_variance=EXCLUDED.family_sharpe_variance,
                 expected_maximum_sharpe=EXCLUDED.expected_maximum_sharpe,p_value=EXCLUDED.p_value,
                 q_value=EXCLUDED.q_value,selection_gate=EXCLUDED.selection_gate,source=EXCLUDED.source,
                 evaluated_at=now()""",
            (family, key[0], Json(item["parameters"]), key[1], return_basis, item.get("sample_start"),
             item.get("sample_end"), evaluation["observations"], evaluation["mean_return"], evaluation["sharpe"],
             evaluation["probabilistic_sharpe"], evaluation["deflated_sharpe"], null["trials"],
             null["trial_sharpe_variance"], null["expected_maximum_sharpe"], evaluation["p_value"],
             evaluation["q_value"], gate, source),
        )
        stored.append({"family": family, "variant_key": key[0], "parameters_hash": key[1], **evaluation,
                       "family_trials": null["trials"], "expected_maximum_sharpe": null["expected_maximum_sharpe"],
                       "selection_gate": gate})
    return stored


def _series(rows: Iterable[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    by_variant: dict[str, dict[str, Any]] = {}
    for row in rows:
        variant = by_variant.setdefault(str(row["variant_key"]), {"returns": [], "dates": []})
        variant["returns"].append(float(row["period_return"]))
        variant["dates"].append(row["period_date"])
    return by_variant


def evaluate_outcome_families(connection: Any, as_of_date: date) -> dict[str, int]:
    """Evaluate the settled outcome lines as families of competing variants.

    Returns are the per-session mean net return of each variant's settled
    ideas, so a session with many ideas counts once.  Holding windows overlap
    (the ledger holds ten sessions), which makes these Sharpes optimistic; the
    DSR's sample floor and the family null are the guard, not a cure.
    """
    ledger = _series(dict(row) for row in connection.execute(
        """SELECT strategy_key AS variant_key, entry_date AS period_date, avg(net_return) AS period_return
             FROM quant.strategy_daily_candidate_outcomes
            WHERE settlement_version=%s AND net_return IS NOT NULL AND exit_date<=%s
            GROUP BY strategy_key, entry_date ORDER BY strategy_key, entry_date""",
        (SETTLEMENT_VERSION, as_of_date),
    ).fetchall())
    xiaojie = _series(dict(row) for row in connection.execute(
        """SELECT mode AS variant_key, trading_date AS period_date,
                  avg((entry_to_next_close_pct - round_trip_cost_pct) / 100.0) AS period_return
             FROM quant.xiaojie_leader_flow_outcomes
            WHERE entry_to_next_close_pct IS NOT NULL AND round_trip_cost_pct IS NOT NULL
              AND NOT sealed_at_entry AND trading_date<%s
            GROUP BY mode, trading_date ORDER BY mode, trading_date""",
        (as_of_date,),
    ).fetchall())
    stored = {}
    for family, series, basis, parameters in (
        ("candidate_ledger", ledger, "session_mean_net_return_t1_settlement", {"settlement": SETTLEMENT_VERSION, "horizon_sessions": 10}),
        ("xiaojie_leader_flow", xiaojie, "session_mean_net_entry_to_next_close", {"exclude_sealed_at_entry": True}),
    ):
        stored[family] = len(record_family(connection, family=family, return_basis=basis, source="outcome_recompute",
                                           variants=[{"variant_key": key, "parameters": parameters,
                                                      "returns": value["returns"],
                                                      "sample_start": min(value["dates"]),
                                                      "sample_end": max(value["dates"])}
                                                     for key, value in series.items()]))
    return stored


LATEST_TRIALS_SQL = """SELECT * FROM (
       SELECT DISTINCT ON (family, variant_key, parameters_hash)
              family,variant_key,parameters,parameters_hash,return_basis,sample_start,sample_end,
              observations,mean_return,sharpe,probabilistic_sharpe,deflated_sharpe,family_trials,
              expected_maximum_sharpe,p_value,q_value,selection_gate,source,live_effect,evaluated_at
         FROM quant.research_trials
        WHERE %s::text IS NULL OR family=%s
        ORDER BY family,variant_key,parameters_hash,evaluated_at DESC,sample_end DESC NULLS LAST) latest
    ORDER BY family, deflated_sharpe DESC NULLS LAST, variant_key
    LIMIT %s"""


def latest_trials_parameters(family: str | None, limit: int) -> tuple[Any, ...]:
    return (family, family, max(1, min(int(limit), 500)))


def trials_payload(rows: list[Any]) -> dict[str, Any]:
    return {
        "items": [dict(row) for row in rows],
        "selection_rule": {"deflated_sharpe_at_least": 0.95, "family_q_value_at_most": 0.10,
                           "minimum_observations": 20},
        "research_only": True, "live_effect": "none",
    }


def latest_trials(connection: Any, family: str | None = None, limit: int = 200) -> dict[str, Any]:
    """Each variant's latest evaluation, best deflated Sharpe first."""
    return trials_payload(connection.execute(LATEST_TRIALS_SQL, latest_trials_parameters(family, limit)).fetchall())


__all__ = [
    "LATEST_TRIALS_SQL", "evaluate_outcome_families", "latest_trials", "latest_trials_parameters", "record_family",
    "trials_payload",
]
