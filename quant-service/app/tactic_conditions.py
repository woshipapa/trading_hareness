"""Pure history features and sample builders for tactic-trial evidence."""

from __future__ import annotations

from math import pow
from typing import Any, Mapping, Sequence

from .limit_up_risk import _seal_minutes, _seal_time

HISTORY_FIELDS = (
    "turnover_vs_max250", "runup_20", "close_vs_high60",
    "cost_share_below_close", "cost_share_0_30_above",
)


def _adjusted(row: Mapping[str, Any], field: str) -> float:
    return float(row[field]) * float(row["adj_factor"])


def _amount_cny(row: Mapping[str, Any]) -> float:
    # The window expects amount in thousand CNY and volume in lots; convert amount once to CNY.
    return float(row["amount"]) * 1000.0


def history_features(window: Sequence[Mapping[str, Any]]) -> dict[str, float | None]:
    """Compute the close-day history features from oldest-first bars."""
    current = window[-1]
    previous = list(window[:-1])
    features: dict[str, float | None] = {}

    if len(previous) < 60:
        features["turnover_vs_max250"] = None
    else:
        max_turnover = max(_amount_cny(row) for row in previous[-250:])
        features["turnover_vs_max250"] = _amount_cny(current) / max_turnover

    if len(window) < 21:
        features["runup_20"] = None
    else:
        features["runup_20"] = _adjusted(current, "close") / _adjusted(window[-21], "close") - 1

    if len(previous) < 60:
        features["close_vs_high60"] = None
    else:
        highest = max(_adjusted(row, "high") for row in previous[-60:])
        features["close_vs_high60"] = _adjusted(current, "close") / highest - 1

    valid = []
    for age, row in enumerate(reversed(previous[-120:]), 1):
        volume = float(row["volume"])
        if volume <= 0:
            continue
        weight = volume * pow(0.5, age / 20.0)
        average_price = _amount_cny(row) / (volume * 100.0) * float(row["adj_factor"])
        valid.append((weight, average_price))
    if len(valid) < 20:
        features["cost_share_below_close"] = None
        features["cost_share_0_30_above"] = None
    else:
        total = sum(weight for weight, _price in valid)
        close = _adjusted(current, "close")
        features["cost_share_below_close"] = sum(weight for weight, price in valid if price < close) / total
        features["cost_share_0_30_above"] = sum(
            weight for weight, price in valid if close <= price < close * 1.30
        ) / total
    return features


def _board_bucket(boards: float) -> str:
    return "1" if boards == 1 else "2" if boards == 2 else "3+"


def build_trial_row(
    decoded: Mapping[str, Any], features: Mapping[str, float | None], risk_row: Mapping[str, Any],
    decoded_rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Build one close-slot row from decoded evidence and the stored risk row."""
    bar = decoded["bar"]
    sector = decoded["sector"]
    return {
        "symbol": decoded["symbol"], "name": decoded["name"], "sector": sector,
        "boards": decoded["boards"], "board_bucket": _board_bucket(float(decoded["boards"])),
        "first_seal_time": _seal_time(decoded["first_seal_time"]).strftime("%H:%M:%S"),
        "seal_money": decoded["seal_money"], "seal_max": decoded["seal_max"],
        "main_net": decoded["main_net"], "turnover": decoded["turnover"], "float_cap": decoded["float_cap"],
        "reopened": decoded["reopened"], "sealed_from_open": _seal_minutes(decoded["first_seal_time"]) == 0,
        "one_word": float(bar["high"]) == float(bar["low"]),
        "vr": risk_row.get("vr"), "gap": risk_row.get("gap"), "risk_rank": risk_row.get("risk_rank"),
        "theme_count": sum(row["sector"] == sector for row in decoded_rows),
        **{field: features[field] for field in HISTORY_FIELDS},
    }


def build_day_context(
    rows: Sequence[Mapping[str, Any]], market_turnover: Any, market_turnover_median60: Any,
    prev_top_board_sealed_again: Any,
) -> dict[str, Any]:
    """Build the close-slot day context from sample rows and aggregate inputs."""
    return {
        "limit_up_count": len(rows),
        "boards_3plus_count": sum(float(row["boards"]) >= 3 for row in rows),
        "market_turnover": market_turnover,
        "market_turnover_median60": market_turnover_median60,
        "prev_top_board_sealed_again": prev_top_board_sealed_again,
    }


__all__ = ["HISTORY_FIELDS", "build_day_context", "build_trial_row", "history_features"]
