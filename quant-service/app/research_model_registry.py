"""Admission checks for offline research model artifacts.

An artifact can be registered for audit while still being ineligible for
validation or promotion. No status returned here grants live strategy effect.
"""

from __future__ import annotations

import re
from typing import Any, Mapping


SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
MIN_VALIDATION_DAYS = 60
MIN_VALIDATION_SAMPLES = 200


def admission(record: Mapping[str, Any]) -> dict[str, Any]:
    """Evaluate metadata completeness without loading or executing an artifact."""
    blockers: list[str] = []
    if not SHA256_RE.fullmatch(str(record.get("artifact_sha256") or "")):
        blockers.append("artifact_sha256_missing_or_invalid")
    for field in ("data_snapshot_key", "feature_contract_version", "label_contract_version"):
        if not str(record.get(field) or ""):
            blockers.append(f"{field}_missing")
    if int(record.get("independent_days") or 0) < MIN_VALIDATION_DAYS:
        blockers.append("less_than_60_independent_days")
    if int(record.get("sample_count") or 0) < MIN_VALIDATION_SAMPLES:
        blockers.append("less_than_200_independent_samples")
    metrics = record.get("metrics") if isinstance(record.get("metrics"), Mapping) else {}
    if not metrics.get("out_of_sample"):
        blockers.append("out_of_sample_metrics_missing")
    return {
        "status": "reviewable" if not blockers else "blocked", "blockers": blockers,
        "model_key": record.get("model_key"), "model_version": record.get("model_version"),
        "research_only": True, "replay_only": True, "live_effect": "none",
        "policy": "Registry metadata does not load models, fit thresholds, or authorize live use.",
    }


__all__ = ["MIN_VALIDATION_DAYS", "MIN_VALIDATION_SAMPLES", "SHA256_RE", "admission"]
