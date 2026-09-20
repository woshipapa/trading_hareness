"""Deterministic, offline-only linear baseline for research model trials.

This module consumes an already exported feature/label table. It never reads
the database or calls a provider, and its output is always research-only. The
normalisation statistics are fitted inside each training fold to prevent
future leakage.
"""

from __future__ import annotations

import math
from datetime import date
from typing import Any, Iterable, Mapping, Sequence

import numpy as np

from .strategy_validation import walk_forward_splits


MIN_INDEPENDENT_DAYS = 60
MIN_SAMPLES = 200
TRAIN_DAYS = 60
TEST_DAYS = 20
EMBARGO_DAYS = 5
TRAINING_VERSION = "offline-logistic-oof-v3"


def _sigmoid(value: float) -> float:
    value = max(-35.0, min(35.0, value))
    return 1.0 / (1.0 + math.exp(-value))


def _date(value: Any) -> date | None:
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def _clean_rows(rows: Iterable[Mapping[str, Any]], feature_names: Sequence[str]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for row in rows:
        exchange_date = _date(row.get("exchange_date"))
        try:
            label = int(row.get("label"))
            values = [float(row[name]) for name in feature_names]
        except (KeyError, TypeError, ValueError):
            continue
        if exchange_date is None or label not in (0, 1) or any(not math.isfinite(value) for value in values):
            continue
        result.append({"exchange_date": exchange_date, "label": label, "values": values})
    return sorted(result, key=lambda item: item["exchange_date"])


def _fit_logistic(
    rows: list[dict[str, Any]],
    feature_count: int,
    *,
    l2: float,
    learning_rate: float,
    iterations: int,
) -> tuple[float, list[float], list[float], list[float]]:
    """Fit a deterministic NumPy baseline with fold-local normalisation."""
    matrix = np.asarray([item["values"] for item in rows], dtype=np.float64)
    labels = np.asarray([item["label"] for item in rows], dtype=np.float64)
    means_array = matrix.mean(axis=0)
    scales_array = matrix.std(axis=0, ddof=1) if len(rows) > 1 else np.ones(feature_count)
    scales_array = np.where(scales_array > 1e-12, scales_array, 1.0)
    matrix = (matrix - means_array) / scales_array
    intercept = 0.0
    weights_array = np.zeros(feature_count, dtype=np.float64)
    for _ in range(iterations):
        logits = np.clip(intercept + matrix @ weights_array, -35.0, 35.0)
        probabilities = 1.0 / (1.0 + np.exp(-logits))
        errors = probabilities - labels
        intercept -= learning_rate * float(errors.mean())
        weights_array -= learning_rate * ((matrix.T @ errors) / len(rows) + l2 * weights_array)
    return (
        intercept,
        weights_array.tolist(),
        means_array.tolist(),
        scales_array.tolist(),
    )


def _predict(row: Mapping[str, Any], parameters: tuple[float, list[float], list[float], list[float]]) -> float:
    intercept, weights, means, scales = parameters
    values = row["values"]
    vector = [(values[index] - means[index]) / scales[index] for index in range(len(weights))]
    return _sigmoid(intercept + sum(weight * value for weight, value in zip(weights, vector, strict=True)))


def _roc_auc(probabilities: list[float], labels: list[int]) -> float | None:
    """Return tie-aware binary ROC AUC without a heavy ML dependency."""
    positives = sum(labels)
    negatives = len(labels) - positives
    if positives == 0 or negatives == 0:
        return None
    ordered = sorted(zip(probabilities, labels, strict=True), key=lambda item: item[0])
    rank_sum, index = 0.0, 0
    while index < len(ordered):
        end = index + 1
        while end < len(ordered) and ordered[end][0] == ordered[index][0]:
            end += 1
        average_rank = (index + 1 + end) / 2.0
        rank_sum += average_rank * sum(label for _, label in ordered[index:end])
        index = end
    return (rank_sum - positives * (positives + 1) / 2) / (positives * negatives)


def _losses(probabilities: list[float], labels: list[int]) -> dict[str, float]:
    clipped = [min(1 - 1e-6, max(1e-6, value)) for value in probabilities]
    brier = sum((probability - label) ** 2 for probability, label in zip(clipped, labels, strict=True)) / len(labels)
    log_loss = -sum(
        label * math.log(probability) + (1 - label) * math.log(1 - probability)
        for probability, label in zip(clipped, labels, strict=True)
    ) / len(labels)
    return {"brier": round(brier, 8), "log_loss": round(log_loss, 8)}


def train_oof(
    rows: Iterable[Mapping[str, Any]],
    *,
    feature_names: Sequence[str],
    l2: float = 0.01,
    learning_rate: float = 0.15,
    iterations: int = 300,
    include_predictions: bool = True,
) -> dict[str, Any]:
    """Fit expanding chronological folds and return OOF diagnostics/artifact."""
    names = tuple(str(name) for name in feature_names if str(name))
    if l2 < 0 or learning_rate <= 0 or iterations <= 0:
        raise ValueError("l2 must be non-negative and learning_rate/iterations must be positive")
    clean = _clean_rows(rows, names)
    dates = sorted({item["exchange_date"] for item in clean})
    base = {
        "version": TRAINING_VERSION, "feature_names": list(names),
        "parameters": {"l2": float(l2), "learning_rate": float(learning_rate), "iterations": int(iterations)},
        "research_only": True, "replay_only": True, "live_effect": "none",
    }
    if not names:
        return {**base, "status": "blocked", "blockers": ["feature_names_missing"]}
    blockers = []
    if len(dates) < MIN_INDEPENDENT_DAYS:
        blockers.append("less_than_60_independent_days")
    if len(clean) < MIN_SAMPLES:
        blockers.append("less_than_200_independent_samples")
    if blockers:
        return {**base, "status": "blocked", "blockers": blockers, "independent_days": len(dates), "samples": len(clean)}
    folds = walk_forward_splits(dates, train_size=TRAIN_DAYS, test_size=TEST_DAYS, embargo=EMBARGO_DAYS)
    predictions: list[dict[str, Any]] = []
    for fold in folds:
        train_dates, test_dates = set(fold.train), set(fold.test)
        train_rows = [item for item in clean if item["exchange_date"] in train_dates]
        test_rows = [item for item in clean if item["exchange_date"] in test_dates]
        if not train_rows or not test_rows:
            continue
        parameters = _fit_logistic(
            train_rows, len(names), l2=l2, learning_rate=learning_rate, iterations=iterations,
        )
        baseline_probability = sum(item["label"] for item in train_rows) / len(train_rows)
        predictions.extend({
            "exchange_date": str(item["exchange_date"]), "label": item["label"],
            "probability": _predict(item, parameters), "fold_train_samples": len(train_rows),
            "baseline_probability": baseline_probability,
        } for item in test_rows)
    if not predictions:
        return {**base, "status": "blocked", "blockers": ["no_oof_fold"], "independent_days": len(dates), "samples": len(clean)}
    probabilities = [float(item["probability"]) for item in predictions]
    labels = [int(item["label"]) for item in predictions]
    baseline_probabilities = [float(item["baseline_probability"]) for item in predictions]
    losses = _losses(probabilities, labels)
    baseline_losses = _losses(baseline_probabilities, labels)
    final_parameters = _fit_logistic(
        clean, len(names), l2=l2, learning_rate=learning_rate, iterations=iterations,
    )
    output = {
        **base, "status": "trained_research_only", "independent_days": len(dates), "samples": len(clean),
        "oof_samples": len(predictions), "oof_days": len({item["exchange_date"] for item in predictions}),
        "folds": len(folds),
        "metrics": {
            "out_of_sample": True,
            **losses,
            "roc_auc": _roc_auc(probabilities, labels),
            "constant_baseline": baseline_losses,
            "beats_constant_log_loss": losses["log_loss"] < baseline_losses["log_loss"],
        },
        "artifact": {"intercept": final_parameters[0], "weights": final_parameters[1], "means": final_parameters[2], "scales": final_parameters[3]},
        "policy": "OOF baseline only; no live loading, threshold fitting, or automatic promotion.",
    }
    if include_predictions:
        output["predictions"] = predictions
    return output


__all__ = ["EMBARGO_DAYS", "MIN_INDEPENDENT_DAYS", "MIN_SAMPLES", "TEST_DAYS", "TRAINING_VERSION", "TRAIN_DAYS", "train_oof"]
