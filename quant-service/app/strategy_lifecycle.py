"""Research-only strategy decay and lifecycle projections.

The evaluator consumes already-settled, per-trading-day metrics.  It emits a
manual-review proposal and never writes a promotion registry or changes a live
threshold.  Persistence/scheduling can adopt this contract without coupling
the pure state machine to a database schema.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence


MIN_INDEPENDENT_DAYS = 20
SEVERITIES = ("healthy", "warning", "decayed", "critical", "insufficient")
STATES = ("active", "monitoring", "decayed", "disable_proposed")


def _number(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed == parsed else None


def assess_decay(
    current: Mapping[str, Any], baseline: Mapping[str, Any], *, min_independent_days: int = MIN_INDEPENDENT_DAYS,
) -> dict[str, Any]:
    """Classify the worst observed decay dimension with explicit data gaps."""
    days = int(current.get("independent_days") or 0)
    if days < max(1, int(min_independent_days)):
        return {
            "severity": "insufficient", "status": "insufficient", "independent_days": days,
            "reasons": ["insufficient-independent-trading-days"], "live_effect": "none",
        }
    reasons: list[str] = []
    severity = "healthy"
    current_net = _number(current.get("net_mean"))
    baseline_net = _number(baseline.get("net_mean"))
    current_hit = _number(current.get("hit_rate"))
    baseline_hit = _number(baseline.get("hit_rate"))
    trigger_ratio = _number(current.get("trigger_ratio"))
    stale_rate = _number(current.get("stale_rejection_rate"))
    if current_net is not None and current_net < 0:
        severity, reasons = "critical", ["negative-net-mean"]
    elif current_hit is not None and baseline_hit is not None and current_hit - baseline_hit <= -0.15:
        severity, reasons = "decayed", ["hit-rate-drop"]
    elif current_net is not None and baseline_net is not None and current_net < baseline_net * 0.5:
        severity, reasons = "decayed", ["net-mean-drop"]
    elif trigger_ratio is not None and (trigger_ratio > 3.0 or trigger_ratio < 0.33):
        severity, reasons = "warning", ["trigger-frequency-drift"]
    if stale_rate is not None and stale_rate >= 0.5:
        if severity in {"healthy", "warning"}:
            severity = "warning"
        reasons.append("stale-rejection-rate-high")
    return {
        "severity": severity, "status": severity, "independent_days": days,
        "reasons": reasons, "live_effect": "none",
        "metrics": {"current": dict(current), "baseline": dict(baseline)},
    }


def transition_state(
    state: str, history: Sequence[str], *, current_severity: str,
) -> dict[str, Any]:
    """Apply the preregistered hysteresis without automatic enable/disable."""
    current_state = state if state in STATES else "active"
    recent = [str(value) for value in history[-3:]]
    unhealthy = current_severity in {"warning", "decayed", "critical"}
    next_state = current_state
    proposal = None
    if current_state == "active" and len(recent) >= 2 and all(value in {"warning", "decayed", "critical"} for value in recent[-2:]):
        next_state = "monitoring"
    elif current_state == "monitoring" and current_severity == "healthy":
        next_state = "active"
    elif current_state == "monitoring" and len(recent) >= 2 and all(value in {"decayed", "critical"} for value in recent[-2:]):
        next_state = "decayed"
    elif current_state == "decayed" and len(recent) >= 3 and all(value == "critical" for value in recent[-3:]):
        next_state = "disable_proposed"
        proposal = "manual_disable_review"
    return {
        "state": next_state, "previous_state": current_state,
        "current_severity": current_severity, "unhealthy": unhealthy,
        "proposal": proposal, "live_effect": "none",
    }


__all__ = ["MIN_INDEPENDENT_DAYS", "SEVERITIES", "STATES", "assess_decay", "transition_state"]
