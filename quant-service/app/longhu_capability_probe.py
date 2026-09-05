"""Research-only summaries for the authenticated Longhu capability proxy.

The proxy deliberately returns vendor payloads to trusted callers.  This
module provides a safe observation surface for dashboards and experiments:
it records request shape and response structure, never the payload itself or
credential-bearing fields.  Nothing returned here is strategy/live input.
"""

from __future__ import annotations

from hashlib import sha256
import json
from typing import Any, Mapping

from .licensed_stock_api import SENSITIVE_QUERY_KEYS


def _safe(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _safe(item)
            for key, item in value.items()
            if str(key).lower() not in SENSITIVE_QUERY_KEYS
        }
    if isinstance(value, (list, tuple)):
        return [_safe(item) for item in value[:20]]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def sanitized_request(request: Mapping[str, Any]) -> dict[str, Any]:
    """Return a bounded, credential-free request projection."""
    result = _safe(request)
    return result if isinstance(result, dict) else {}


def _shape(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        keys = sorted(
            str(key) for key in value
            if str(key).lower() not in SENSITIVE_QUERY_KEYS
        )[:80]
        return {"type": "object", "keys": keys}
    if isinstance(value, list):
        item_shapes: list[dict[str, Any]] = []
        for item in value[:3]:
            shape = _shape(item)
            if shape not in item_shapes:
                item_shapes.append(shape)
        return {"type": "array", "length": len(value), "item_shapes": item_shapes}
    if value is None:
        return {"type": "null"}
    return {"type": type(value).__name__}


def summarize_result(result: Mapping[str, Any]) -> dict[str, Any]:
    """Summarize a stock-api response without echoing market rows."""
    pages = result.get("pages")
    page_items = pages if isinstance(pages, list) else []
    summaries: list[dict[str, Any]] = []
    errcodes: list[Any] = []
    for page in page_items[:300]:
        payload = page.get("payload") if isinstance(page, Mapping) else page
        if isinstance(payload, Mapping) and "errcode" in payload:
            errcodes.append(payload.get("errcode"))
        summaries.append(_shape(payload))
    status = "completed"
    if not page_items or any(code not in (0, "0", None) for code in errcodes):
        status = "partial" if page_items else "failed"
    projection = {
        "target": result.get("target"),
        "calls": int(result.get("calls") or len(page_items)),
        "pages": len(page_items),
        "status": status,
        "errcodes": errcodes[:20],
        "page_shapes": summaries,
        "research_only": True,
        "replay_only": True,
        "live_effect": "none",
    }
    canonical = json.dumps(projection, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    projection["summary_sha256"] = sha256(canonical.encode("utf-8")).hexdigest()
    return projection


__all__ = ["sanitized_request", "summarize_result"]
