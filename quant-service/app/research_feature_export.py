"""Immutable, offline feature/label export contract for research trials.

The exporter deliberately accepts an already materialised table.  It does not
query PostgreSQL, fetch providers, infer labels or write a model registry row.
Its only job is to make the input contract explicit and bind the exact rows to
a deterministic SHA-256 snapshot identity before an offline trainer consumes
them.
"""

from __future__ import annotations

from datetime import date, datetime
import math
from typing import Any, Iterable, Mapping, Sequence

from .research_manifest import canonical_json, manifest_digest


def _date(value: Any) -> date | None:
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def _aware(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
    return parsed if parsed.tzinfo is not None and parsed.utcoffset() is not None else None


def export_training_table(
    rows: Iterable[Mapping[str, Any]],
    *,
    feature_names: Sequence[str],
    feature_contract_version: str,
    label_contract_version: str,
) -> dict[str, Any]:
    """Validate and fingerprint a pre-materialised point-in-time table.

    Each row must contain ``symbol``, ``exchange_date``, a nested ``features``
    mapping and a binary ``label``.  If ``feature_available_at`` and
    ``label_available_at`` are supplied, the former must not be after the
    latter.  Invalid rows are rejected as a whole rather than silently
    dropping observations, preventing a trainer from receiving an accidental
    partial export.
    """

    names = tuple(dict.fromkeys(str(name) for name in feature_names if str(name)))
    feature_version = str(feature_contract_version or "").strip()
    label_version = str(label_contract_version or "").strip()
    base = {
        "feature_contract_version": feature_version,
        "label_contract_version": label_version,
        "feature_names": list(names),
        "research_only": True,
        "replay_only": True,
        "live_effect": "none",
    }
    if not names:
        return {**base, "status": "blocked", "blockers": ["feature_names_missing"]}
    if not feature_version or not label_version:
        return {**base, "status": "blocked", "blockers": ["contract_version_missing"]}

    normalised: list[dict[str, Any]] = []
    seen: set[tuple[str, date]] = set()
    errors: list[str] = []
    for index, row in enumerate(rows):
        symbol = str(row.get("symbol") or "").strip()
        exchange_date = _date(row.get("exchange_date"))
        features = row.get("features")
        key = (symbol, exchange_date) if symbol and exchange_date else None
        if not symbol or exchange_date is None or not isinstance(features, Mapping):
            errors.append(f"row_{index}_identity_or_features_invalid")
            continue
        try:
            label = int(row.get("label"))
        except (TypeError, ValueError):
            errors.append(f"row_{index}_label_invalid")
            continue
        if label not in (0, 1):
            errors.append(f"row_{index}_label_not_binary")
        if key in seen:
            errors.append(f"row_{index}_duplicate_symbol_date")
        seen.add(key)
        values: dict[str, float] = {}
        for name in names:
            try:
                value = float(features[name])
            except (KeyError, TypeError, ValueError):
                errors.append(f"row_{index}_feature_missing:{name}")
                continue
            if not math.isfinite(value):
                errors.append(f"row_{index}_feature_non_finite:{name}")
                continue
            values[name] = value
        feature_at = _aware(row.get("feature_available_at")) if row.get("feature_available_at") is not None else None
        label_at = _aware(row.get("label_available_at")) if row.get("label_available_at") is not None else None
        if row.get("feature_available_at") is not None and feature_at is None:
            errors.append(f"row_{index}_feature_available_at_not_aware")
        if row.get("label_available_at") is not None and label_at is None:
            errors.append(f"row_{index}_label_available_at_not_aware")
        if feature_at is not None and label_at is not None and feature_at > label_at:
            errors.append(f"row_{index}_feature_after_label_availability")
        normalised.append({
            "symbol": symbol,
            "exchange_date": exchange_date.isoformat(),
            "label": label,
            "features": {name: values.get(name) for name in names},
            **({"feature_available_at": feature_at.isoformat()} if feature_at else {}),
            **({"label_available_at": label_at.isoformat()} if label_at else {}),
        })
    if errors:
        return {**base, "status": "blocked", "blockers": sorted(set(errors)), "rows": len(normalised)}
    normalised.sort(key=lambda item: (item["exchange_date"], item["symbol"]))
    manifest = {
        "contract": {"feature": feature_version, "label": label_version},
        "feature_names": list(names),
        "rows": normalised,
    }
    digest = manifest_digest(manifest)
    return {
        **base,
        "status": "exported_research_only",
        "rows": normalised,
        "samples": len(normalised),
        "independent_days": len({item["exchange_date"] for item in normalised}),
        "manifest_digest": digest,
        "data_snapshot_key": f"featureset:{digest}",
        "canonical_manifest": canonical_json(manifest),
    }


__all__ = ["export_training_table"]
