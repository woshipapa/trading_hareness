"""Count every variant a research family compares, and deflate for it.

A deflated Sharpe is only as honest as its trial count.  The count here is
the number of distinct variant configurations a family has ever evaluated -
each factor and horizon, each ledger strategy, each xiaojie mode - not the
number of times one of them was recomputed as its sample grew, which would
inflate the null for no reason.  The dispersion of their latest Sharpes sets
how lucky the best of them could be by chance (strategy_validation).

Every family also gets Benjamini-Hochberg q-values over its variants, from
each variant's probabilistic Sharpe against zero, so a family of twenty modes
cannot report its best one as a finding without the correction attached.

All of this is evidence.  ``selection_gate`` records whether a variant clears
the documented bar; nothing here changes a live threshold or weight.
"""

from __future__ import annotations

import hashlib
import json
from statistics import pvariance
from typing import Any, Mapping, Sequence

from .strategy_validation import (
    MINIMUM_EVALUABLE_OBSERVATIONS, deflated_sharpe_ratio, expected_maximum_sharpe,
    probabilistic_sharpe_ratio, sharpe_ratio,
)

#: A variant clears selection only with this deflated-Sharpe probability.
DSR_SELECTION_THRESHOLD = 0.95
#: And with this FDR q-value within its family.
FDR_SELECTION_THRESHOLD = 0.10


def parameters_hash(parameters: Mapping[str, Any]) -> str:
    return hashlib.sha256(json.dumps(parameters, sort_keys=True, default=str).encode()).hexdigest()[:16]


def family_null(latest_sharpes: Sequence[float]) -> dict[str, Any]:
    """The selection null implied by a family's variants."""
    values = [float(value) for value in latest_sharpes if value is not None]
    trials = len(values)
    variance = pvariance(values) if trials >= 2 else 0.0
    return {
        "trials": trials,
        "trial_sharpe_variance": variance,
        "expected_maximum_sharpe": expected_maximum_sharpe(trials=trials, trial_sharpe_variance=variance),
    }


def bh_q_values(p_values: Mapping[str, float | None]) -> dict[str, float | None]:
    """Benjamini-Hochberg adjusted q-values; None stays None and is not counted."""
    tested = sorted(((key, value) for key, value in p_values.items() if value is not None), key=lambda item: item[1])
    count = len(tested)
    q_values: dict[str, float | None] = {key: None for key in p_values}
    running = 1.0
    for rank in range(count, 0, -1):
        key, value = tested[rank - 1]
        running = min(running, value * count / rank)
        q_values[key] = running
    return q_values


def evaluate_returns(returns: Sequence[float], *, null: Mapping[str, Any]) -> dict[str, Any]:
    """Sharpe, PSR against zero and DSR against the family null."""
    series = [float(value) for value in returns if value is not None]
    sharpe = sharpe_ratio(series) if len(series) >= 2 else None
    evaluable = len(series) >= MINIMUM_EVALUABLE_OBSERVATIONS and sharpe is not None
    psr = probabilistic_sharpe_ratio(series) if evaluable else None
    dsr = (deflated_sharpe_ratio(series, trials=max(1, int(null["trials"])),
                                 trial_sharpe_variance=float(null["trial_sharpe_variance"]))
           if evaluable else None)
    return {
        "observations": len(series),
        "mean_return": sum(series) / len(series) if series else None,
        "sharpe": sharpe,
        "probabilistic_sharpe": psr,
        "deflated_sharpe": dsr,
        "p_value": (1 - psr) if psr is not None else None,
        "evaluable": evaluable,
    }


def selection_gate(evaluation: Mapping[str, Any]) -> str:
    if not evaluation.get("evaluable"):
        return "insufficient_sample"
    dsr, q_value = evaluation.get("deflated_sharpe"), evaluation.get("q_value")
    if dsr is None or dsr < DSR_SELECTION_THRESHOLD:
        return "fails_deflated_sharpe"
    if q_value is None or q_value > FDR_SELECTION_THRESHOLD:
        return "fails_family_fdr"
    return "clears_selection_evidence_only"


__all__ = [
    "DSR_SELECTION_THRESHOLD", "FDR_SELECTION_THRESHOLD", "bh_q_values", "evaluate_returns", "family_null",
    "parameters_hash", "selection_gate",
]
