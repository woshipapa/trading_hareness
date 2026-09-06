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

from .strategy_validation import walk_forward_splits


MIN_INDEPENDENT_DAYS = 60
MIN_SAMPLES = 200
TRAIN_DAYS = 60
TEST_DAYS = 20
EMBARGO_DAYS = 5
TRAINING_VERSION = "offline-logistic-oof-v1"


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


def _fit_logistic(rows: list[dict[str, Any]], feature_count: int) -> tuple[float, list[float], list[float], list[float]]:
    means = [sum(item["values"][index] for item in rows) / len(rows) for index in range(feature_count)]
    scales = []
    for index, mean in enumerate(means):
        variance = sum((item["values"][index] - mean) ** 2 for item in rows) / max(1, len(rows) - 1)
        scales.append(math.sqrt(variance) if variance > 1e-12 else 1.0)
    intercept, weights = 0.0, [0.0] * feature_count
    for _ in range(300):
        grad_i = 0.0
        grad_w = [0.0] * feature_count
        for item in rows:
            vector = [(item["values"][index] - means[index]) / scales[index] for index in range(feature_count)]
            probability = _sigmoid(intercept + sum(weight * value for weight, value in zip(weights, vector, strict=True)))
            error = probability - item["label"]
            grad_i += error
            for index, value in enumerate(vector):
                grad_w[index] += error * value + 0.01 * weights[index]
        divisor = max(1, len(rows))
        intercept -= 0.15 * grad_i / divisor
        weights = [weight - 0.15 * gradient / divisor for weight, gradient in zip(weights, grad_w, strict=True)]
    return intercept, weights, means, scales


def _predict(row: Mapping[str, Any], parameters: tuple[float, list[float], list[float], list[float]]) -> float:
    intercept, weights, means, scales = parameters
    values = row["values"]
    vector = [(values[index] - means[index]) / scales[index] for index in range(len(weights))]
    return _sigmoid(intercept + sum(weight * value for weight, value in zip(weights, vector, strict=True)))


def train_oof(rows: Iterable[Mapping[str, Any]], *, feature_names: Sequence[str]) -> dict[str, Any]:
    """Fit expanding chronological folds and return OOF diagnostics/artifact."""
    names = tuple(str(name) for name in feature_names if str(name))
    clean = _clean_rows(rows, names)
    dates = sorted({item["exchange_date"] for item in clean})
    base = {
        "version": TRAINING_VERSION, "feature_names": list(names),
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
        parameters = _fit_logistic(train_rows, len(names))
        predictions.extend({
            "exchange_date": str(item["exchange_date"]), "label": item["label"],
            "probability": _predict(item, parameters), "fold_train_samples": len(train_rows),
        } for item in test_rows)
    if not predictions:
        return {**base, "status": "blocked", "blockers": ["no_oof_fold"], "independent_days": len(dates), "samples": len(clean)}
    probabilities = [float(item["probability"]) for item in predictions]
    labels = [int(item["label"]) for item in predictions]
    clipped = [min(1 - 1e-6, max(1e-6, value)) for value in probabilities]
    brier = sum((probability - label) ** 2 for probability, label in zip(clipped, labels, strict=True)) / len(labels)
    log_loss = -sum(label * math.log(probability) + (1 - label) * math.log(1 - probability) for probability, label in zip(clipped, labels, strict=True)) / len(labels)
    final_parameters = _fit_logistic(clean, len(names))
    return {
        **base, "status": "trained_research_only", "independent_days": len(dates), "samples": len(clean),
        "oof_samples": len(predictions), "oof_days": len({item["exchange_date"] for item in predictions}),
        "folds": len(folds), "metrics": {"out_of_sample": True, "brier": round(brier, 8), "log_loss": round(log_loss, 8)},
        "artifact": {"intercept": final_parameters[0], "weights": final_parameters[1], "means": final_parameters[2], "scales": final_parameters[3]},
        "predictions": predictions,
        "policy": "OOF baseline only; no live loading, threshold fitting, or automatic promotion.",
    }


__all__ = ["EMBARGO_DAYS", "MIN_INDEPENDENT_DAYS", "MIN_SAMPLES", "TEST_DAYS", "TRAINING_VERSION", "TRAIN_DAYS", "train_oof"]
