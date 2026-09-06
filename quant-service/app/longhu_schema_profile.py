"""Value-free schema observations for unreviewed Longhu provider payloads."""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable, Mapping


MAX_SCHEMA_DEPTH = 5
MAX_FIELDS_PER_CONTRACT = 120


def _value_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, Mapping):
        return "object"
    if isinstance(value, (list, tuple)):
        return "array"
    return type(value).__name__


def _observe(value: Any, path: str, fields: dict[str, dict[str, Any]], depth: int = 0) -> None:
    if len(fields) >= MAX_FIELDS_PER_CONTRACT:
        return
    entry = fields.setdefault(path or "$", {"path": path or "$", "types": set(), "observations": 0})
    entry["types"].add(_value_type(value))
    entry["observations"] += 1
    if depth >= MAX_SCHEMA_DEPTH:
        return
    if isinstance(value, Mapping):
        for key, child in value.items():
            child_path = f"{path}.{key}" if path else str(key)
            _observe(child, child_path, fields, depth + 1)
    elif isinstance(value, (list, tuple)):
        # A list's values are deliberately not returned; sampling a bounded
        # prefix only discovers structural paths such as ``list[].StockID``.
        for child in value[:20]:
            _observe(child, f"{path}[]", fields, depth + 1)


def profile(rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Summarize observed field paths, never provider values or factor values."""
    groups: dict[tuple[str, str], dict[str, Any]] = defaultdict(lambda: {"rows": 0, "fields": {}})
    for row in rows:
        target, action = str(row.get("target") or ""), str(row.get("action") or "")
        payload = row.get("payload")
        if not target or not action or payload is None:
            continue
        group = groups[(target, action)]
        group["rows"] += 1
        _observe(payload, "", group["fields"])
    contracts = []
    for (target, action), group in sorted(groups.items()):
        fields = [
            {**item, "types": sorted(item["types"])}
            for _path, item in sorted(group["fields"].items())
        ]
        contracts.append({
            "target": target, "action": action, "observed_rows": group["rows"],
            "fields": fields, "schema_review_status": "unreviewed",
            "factor_eligible": False,
        })
    return {
        "status": "observed" if contracts else "empty",
        "research_only": True, "replay_only": True, "live_effect": "none",
        "contracts": contracts,
        "policy": (
            "Field paths and primitive types are observational metadata only. "
            "No field meaning, unit, factor or strategy eligibility is inferred from this profile."
        ),
    }


__all__ = ["MAX_FIELDS_PER_CONTRACT", "MAX_SCHEMA_DEPTH", "profile"]
