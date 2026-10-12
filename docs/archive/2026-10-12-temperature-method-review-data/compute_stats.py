#!/usr/bin/env python3
"""Recompute Phase 3 statistics from the saved owner GET responses."""

from __future__ import annotations

import glob
import json
import math
import os
from collections import defaultdict
from statistics import mean


ROOT = os.path.dirname(__file__)
DAILY_PATH = os.path.join(ROOT, "daily.json")
COMPONENTS = ("limit_up", "limit_down", "seal_rate", "up_ratio", "premium", "promotion", "max_streak", "turnover_ratio")
SCORE_COMPONENTS = COMPONENTS
TARGET_TIMES = ("09:35", "10:30", "11:30", "14:00")


def rankdata(values: list[float]) -> list[float]:
    ordered = sorted(enumerate(values), key=lambda item: item[1])
    ranks = [0.0] * len(values)
    pos = 0
    while pos < len(ordered):
        end = pos + 1
        while end < len(ordered) and ordered[end][1] == ordered[pos][1]:
            end += 1
        rank = (pos + 1 + end) / 2.0
        for index, _value in ordered[pos:end]:
            ranks[index] = rank
        pos = end
    return ranks


def pearson(left: list[float], right: list[float]) -> float | None:
    if len(left) < 2:
        return None
    left_mean, right_mean = mean(left), mean(right)
    left_dev = [value - left_mean for value in left]
    right_dev = [value - right_mean for value in right]
    denom = math.sqrt(sum(value * value for value in left_dev) * sum(value * value for value in right_dev))
    return sum(a * b for a, b in zip(left_dev, right_dev)) / denom if denom else None


def spearman(left: list[float], right: list[float]) -> float | None:
    return pearson(rankdata(left), rankdata(right))


def jacobi_eigenvalues(matrix: list[list[float]]) -> list[float]:
    """Eigenvalues for a small symmetric matrix, using Jacobi rotations."""
    size = len(matrix)
    values = [row[:] for row in matrix]
    for _ in range(100 * size * size):
        p, q = 0, 1
        largest = 0.0
        for i in range(size):
            for j in range(i + 1, size):
                if abs(values[i][j]) > largest:
                    largest, p, q = abs(values[i][j]), i, j
        if largest < 1e-12:
            break
        angle = 0.5 * math.atan2(2.0 * values[p][q], values[q][q] - values[p][p])
        cosine, sine = math.cos(angle), math.sin(angle)
        for i in range(size):
            if i in (p, q):
                continue
            vip, viq = values[i][p], values[i][q]
            values[i][p] = values[p][i] = cosine * vip - sine * viq
            values[i][q] = values[q][i] = sine * vip + cosine * viq
        vpp, vqq, vpq = values[p][p], values[q][q], values[p][q]
        values[p][p] = cosine * cosine * vpp - 2 * sine * cosine * vpq + sine * sine * vqq
        values[q][q] = sine * sine * vpp + 2 * sine * cosine * vpq + cosine * cosine * vqq
        values[p][q] = values[q][p] = 0.0
    return sorted((values[i][i] for i in range(size)), reverse=True)


def band(value: float | None) -> str | None:
    if value is None:
        return None
    if value <= 20:
        return "冰点"
    if value < 40:
        return "冷"
    if value < 60:
        return "中性"
    if value < 80:
        return "热"
    return "沸点"


def mean_range(values: list[float]) -> dict[str, float | int | None]:
    return {"n": len(values), "mean": round(mean(values), 4) if values else None,
            "min": round(min(values), 4) if values else None, "max": round(max(values), 4) if values else None}


def quintile_labels(rows: list[dict], field: str) -> dict[int, int]:
    usable = [(index, metric(row, field)) for index, row in enumerate(rows) if metric(row, field) is not None]
    ordered = sorted(usable, key=lambda item: (item[1], item[0]))
    labels: dict[int, int] = {}
    for position, (index, _value) in enumerate(ordered):
        labels[index] = min(4, position * 5 // len(ordered))
    return labels


def forward_groups(rows: list[dict], key: str, labels: dict[int, int], horizon: int) -> dict[str, dict[str, float | int | None]]:
    grouped: dict[int, list[float]] = defaultdict(list)
    for index, row in enumerate(rows[:-horizon]):
        if metric(row, key) is None or row.get("index_close") is None or rows[index + horizon].get("index_close") is None:
            continue
        grouped[labels[index]].append((rows[index + horizon]["index_close"] / row["index_close"] - 1.0) * 100.0)
    return {str(group): mean_range(values) for group, values in sorted(grouped.items())}


def metric(row: dict, key: str) -> float | None:
    value = row.get(key)
    if value is None:
        value = (row.get("scores") or {}).get(key)
    return float(value) if value is not None else None


def forward_band(rows: list[dict], horizon: int) -> dict[str, dict[str, float | int | None]]:
    grouped: dict[str, list[float]] = defaultdict(list)
    for index, row in enumerate(rows[:-horizon]):
        if row.get("band") is None or row.get("index_close") is None or rows[index + horizon].get("index_close") is None:
            continue
        grouped[row["band"]].append((rows[index + horizon]["index_close"] / row["index_close"] - 1.0) * 100.0)
    return {group: mean_range(values) for group, values in grouped.items()}


def spell_lengths(rows: list[dict], target: str) -> dict[str, float | int | None]:
    lengths: list[int] = []
    current = 0
    for row in rows:
        if row.get("band") == target:
            current += 1
        elif current:
            lengths.append(current)
            current = 0
    if current:
        lengths.append(current)
    return {"spells": len(lengths), "mean_days": round(mean(lengths), 4) if lengths else None,
            "max_days": max(lengths) if lengths else None, "lengths": lengths}


def self_exclusive(rows: list[dict]) -> dict:
    changes: list[float] = []
    band_changes = 0
    compared: list[dict] = []
    for index, row in enumerate(rows):
        if index < 60 or row.get("temperature") is None:
            continue
        values = row.get("values") or {}
        available = []
        for component in COMPONENTS:
            value = values.get(component)
            history = [prior.get("values", {}).get(component) for prior in rows[max(0, index - 250):index]]
            history = [float(item) for item in history if item is not None]
            if value is None or len(history) < 60:
                continue
            ordered = sorted(history)
            below = sum(item < value for item in ordered)
            ties = sum(item == value for item in ordered)
            rank = 100.0 * (below + 0.5 * ties) / len(ordered)
            if component == "limit_down":
                rank = 100.0 - rank
            available.append(rank)
        if len(available) < 5:
            continue
        prior_temperature = round(sum(available) / len(available), 1)
        delta = prior_temperature - float(row["temperature"])
        changes.append(delta)
        prior_band, stored_band = band(prior_temperature), row.get("band")
        band_changes += prior_band != stored_band
        compared.append({"trade_date": row["trade_date"], "stored": row["temperature"], "prior_only": prior_temperature,
                         "delta": round(delta, 4), "stored_band": stored_band, "prior_only_band": prior_band})
    return {"n": len(changes), "mean_abs_difference": round(mean(abs(value) for value in changes), 4) if changes else None,
            "mean_difference": round(mean(changes), 4) if changes else None, "band_changes": band_changes,
            "comparisons": compared}


def intraday_gaps(rows: list[dict], daily_by_date: dict[str, dict]) -> dict:
    by_time: dict[str, list[float]] = defaultdict(list)
    per_day: list[dict] = []
    for item in rows:
        day = item["trade_date"]
        final = daily_by_date.get(day, {}).get("temperature")
        samples = item.get("samples") or []
        sample_by_time = {sample.get("time"): sample for sample in samples}
        record = {"trade_date": day, "daily_temperature": final, "samples": len(samples), "gaps": {}}
        for target in (*TARGET_TIMES, "last"):
            sample = samples[-1] if target == "last" and samples else sample_by_time.get(target)
            value = sample.get("temperature") if sample else None
            delta = value - final if value is not None and final is not None else None
            record["gaps"][target] = round(delta, 4) if delta is not None else None
            if delta is not None:
                by_time[target].append(delta)
        per_day.append(record)
    return {"days": per_day, "summary": {key: mean_range(by_time.get(key, []))
                                            for key in (*TARGET_TIMES, "last")}}


def main() -> None:
    with open(DAILY_PATH, encoding="utf-8") as handle:
        daily = json.load(handle)
    rows = daily["readings"]
    intraday = []
    for path in sorted(glob.glob(os.path.join(ROOT, "intraday-*.json"))):
        with open(path, encoding="utf-8") as handle:
            intraday.append(json.load(handle))
    daily_by_date = {row["trade_date"]: row for row in rows}
    missing = {row["trade_date"]: [key for key in COMPONENTS if (row.get("values") or {}).get(key) is None]
               for row in rows if any((row.get("values") or {}).get(key) is None for key in COMPONENTS)}

    correlations: dict[str, dict[str, dict[str, float | int | None]]] = {}
    for left_index, left in enumerate(COMPONENTS):
        correlations[left] = {}
        for right in COMPONENTS[left_index:]:
            pairs = [(row.get("scores", {}).get(left), row.get("scores", {}).get(right)) for row in rows]
            pairs = [(float(a), float(b)) for a, b in pairs if a is not None and b is not None]
            correlations[left][right] = {"n": len(pairs), "rho": round(spearman([a for a, _ in pairs], [b for _, b in pairs]), 4)
                                          if pairs else None}
            if right != left:
                correlations[right] = correlations.get(right, {})
                correlations[right][left] = correlations[left][right]

    complete_scores = [[float(row["scores"][key]) for key in COMPONENTS] for row in rows
                       if all(row.get("scores", {}).get(key) is not None for key in COMPONENTS)]
    means = [mean(column) for column in zip(*complete_scores)] if complete_scores else []
    standard = []
    for column_index in range(len(COMPONENTS)):
        column = [row[column_index] for row in complete_scores]
        center = means[column_index]
        spread = math.sqrt(sum((value - center) ** 2 for value in column) / len(column))
        standard.append([(value - center) / spread if spread else 0.0 for value in column])
    correlation_matrix = [[sum(standard[col_a][row] * standard[col_b][row] for row in range(len(complete_scores))) / len(complete_scores)
                           for col_b in range(len(COMPONENTS))] for col_a in range(len(COMPONENTS))] if complete_scores else []
    eigenvalues = jacobi_eigenvalues(correlation_matrix) if correlation_matrix else []
    total_variance = sum(eigenvalues)

    temp_labels = quintile_labels(rows, "temperature")
    temperature_diffs = {str(horizon): {"quintiles": forward_groups(rows, "temperature", temp_labels, horizon),
                                         "bands": forward_band(rows, horizon)} for horizon in (1, 3, 5)}
    complete_temperature_rows = [row for row in rows if row.get("temperature") is not None and all(row.get("scores", {}).get(key) is not None for key in COMPONENTS)]
    deviations = {key: mean(abs(float(row["scores"][key]) - float(row["temperature"])) for row in complete_temperature_rows)
                  for key in COMPONENTS}
    distinct_components = sorted(COMPONENTS, key=lambda key: deviations[key], reverse=True)[:2]
    component_forward = {}
    for key in distinct_components:
        labels = quintile_labels(rows, key)
        component_forward[key] = {str(horizon): {"quintiles": forward_groups(rows, key, labels, horizon),
                                                  "bands": forward_band([{**row, "band": band(row.get("scores", {}).get(key))} for row in rows], horizon)}
                                 for horizon in (1, 3, 5)}

    bands = {name: sum(row.get("band") == name for row in rows) for name in ("冰点", "冷", "中性", "热", "沸点")}
    valid_temperatures = [row for row in rows if row.get("temperature") is not None]
    distribution = {"n": len(valid_temperatures), "at_or_below_20": sum(row["temperature"] <= 20 for row in valid_temperatures),
                    "between_20_and_80": sum(20 < row["temperature"] < 80 for row in valid_temperatures),
                    "at_or_above_80": sum(row["temperature"] >= 80 for row in valid_temperatures), "bands": bands,
                    "cold_spells": spell_lengths(rows, "冰点"), "hot_spells": spell_lengths(rows, "沸点")}

    result = {
        "method": {"spearman": "pairwise complete score rows; average ranks for ties",
                   "pca": "standardized eight score columns, listwise complete rows, correlation-matrix PCA",
                   "forward": "close-to-close percent change at 1/3/5 subsequent returned trading rows; quintiles sorted by current value",
                   "self_exclusive": "prior values only, last 250 available values, same tie rank and direction; rows require >=60 prior values and >=5 components",
                   "intraday_gap": "intraday temperature minus same-date daily temperature; missing requested samples omitted from that time's mean"},
        "coverage": {"daily_n": len(rows), "daily_first": rows[0]["trade_date"], "daily_last": rows[-1]["trade_date"],
                     "all_eight_values": sum(not missing.get(row["trade_date"]) for row in rows), "missing_components": missing,
                     "pca_complete_scores_n": len(complete_scores)},
        "redundancy": {"spearman": correlations, "pca_n": len(complete_scores), "eigenvalues": [round(value, 6) for value in eigenvalues],
                       "first_two_variance_share": round(sum(eigenvalues[:2]) / total_variance, 6) if total_variance else None},
        "distribution": distribution,
        "forward_temperature": temperature_diffs,
        "most_deviant_components": {"mean_abs_score_temperature": {key: round(value, 4) for key, value in deviations.items()},
                                     "selected": distinct_components, "forward": component_forward},
        "self_exclusive": self_exclusive(rows),
        "intraday": intraday_gaps(intraday, daily_by_date),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
