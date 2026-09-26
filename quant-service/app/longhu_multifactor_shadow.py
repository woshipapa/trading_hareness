"""Transparent Longhu multi-factor shadow scorer.

This is intentionally a pure, research-only scorer.  Inputs are normalized
features rather than raw vendor fields, so each upstream operation can be
decoded and validated independently before it is used.  Missing or stale
evidence reduces coverage and blocks a candidate instead of being imputed.
"""

from __future__ import annotations

import math
from typing import Any, Mapping


MODEL_VERSION = "longhu-multifactor-shadow-v1"
STRATEGY_KEY = "longhu_multifactor_shadow"

WEIGHTS = {
    "quote_strength": 0.10,
    "minute_momentum": 0.20,
    "volume_confirmation": 0.12,
    "order_book_balance": 0.18,
    "large_order_flow": 0.15,
    "board_relative_strength": 0.15,
    "auction_strength": 0.10,
}

REQUIRED = tuple(WEIGHTS)


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _clip(value: float, lower: float = 0.0, upper: float = 1.0) -> float:
    return min(upper, max(lower, value))


def _scale(value: float, low: float, high: float) -> float:
    if high <= low:
        return 0.0
    return _clip((value - low) / (high - low))


def build_features(raw: Mapping[str, Any]) -> dict[str, float | None]:
    """Map source-neutral observations to seven bounded factor values.

    Callers must provide already normalized values.  The function only applies
    documented ranges; it does not guess units or convert vendor fields.
    """
    return {
        "quote_strength": _scale(float(raw["quote_return_pct"]), -3.0, 8.0)
        if _finite(raw.get("quote_return_pct")) is not None else None,
        "minute_momentum": _scale(float(raw["minute_momentum_pct"]), -1.5, 3.0)
        if _finite(raw.get("minute_momentum_pct")) is not None else None,
        "volume_confirmation": _scale(float(raw["volume_ratio"]), 0.5, 3.0)
        if _finite(raw.get("volume_ratio")) is not None else None,
        "order_book_balance": _scale(float(raw["order_book_imbalance"]), -1.0, 1.0)
        if _finite(raw.get("order_book_imbalance")) is not None else None,
        "large_order_flow": _scale(float(raw["large_order_net_ratio"]), -1.0, 1.0)
        if _finite(raw.get("large_order_net_ratio")) is not None else None,
        "board_relative_strength": _scale(float(raw["board_relative_pct"]), -3.0, 5.0)
        if _finite(raw.get("board_relative_pct")) is not None else None,
        "auction_strength": _scale(float(raw["auction_premium_pct"]), -3.0, 8.0)
        if _finite(raw.get("auction_premium_pct")) is not None else None,
    }


def score_candidate(
    features: Mapping[str, Any],
    *,
    stale_seconds: float | None = None,
    coverage: float | None = None,
    risk_flags: list[str] | None = None,
) -> dict[str, Any]:
    """Score one candidate without creating a live signal or order."""
    values = {key: _finite(features.get(key)) for key in REQUIRED}
    available = [key for key, value in values.items() if value is not None]
    denominator = sum(WEIGHTS[key] for key in available)
    weighted = sum(float(values[key]) * WEIGHTS[key] for key in available)
    factor_coverage = (denominator / sum(WEIGHTS.values())) if denominator else 0.0
    if coverage is not None:
        factor_coverage = min(factor_coverage, _clip(float(coverage)))
    flags = list(risk_flags or [])
    if stale_seconds is not None and (not math.isfinite(float(stale_seconds)) or float(stale_seconds) > 30):
        flags.append("stale_evidence")
    if factor_coverage < 0.70:
        flags.append("insufficient_factor_coverage")
    if flags:
        state = "blocked" if "stale_evidence" in flags or factor_coverage < 0.70 else "observe"
    else:
        state = "shadow_candidate" if weighted / denominator >= 0.62 else "observe"
    normalized = (weighted / denominator) if denominator else 0.0
    return {
        "strategy_key": STRATEGY_KEY,
        "model_version": MODEL_VERSION,
        "state": state,
        "score": round(100.0 * _clip(normalized), 4),
        "factor_coverage": round(factor_coverage, 6),
        "available_factors": available,
        "missing_factors": [key for key in REQUIRED if key not in available],
        "risk_flags": sorted(set(flags)),
        "components": {
            key: round(float(values[key]), 6)
            for key in available
        },
        "research_only": True,
        "live_effect": "none",
        "promotion_record_required": True,
    }


__all__ = ["MODEL_VERSION", "STRATEGY_KEY", "WEIGHTS", "build_features", "score_candidate"]
