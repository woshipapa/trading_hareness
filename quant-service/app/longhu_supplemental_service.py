"""Bounded post-close Longhu supplemental evidence capture."""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Awaitable, Callable

from .longhu_research_features import normalize_payload


SUPPLEMENTAL_REQUESTS: tuple[dict[str, Any], ...] = (
    {"target": "longhu_market_wide", "action": "RiseFallAnalysis", "controller": "HomeDingPan", "params": {"a": "RiseFallAnalysis", "apiv": "w43", "c": "HomeDingPan", "PhoneOSNew": 1, "VerSion": "5.22.0.2"}},
    {"target": "longhu_market_wide", "action": "MoodNumCount", "controller": "MarketMood", "params": {"a": "MoodNumCount", "apiv": "w43", "c": "MarketMood", "PhoneOSNew": 1, "VerSion": "5.22.0.2"}},
    {"target": "longhu_market_wide", "action": "GetPlateInfo_w38", "controller": "DailyLimitResumption", "params": {"a": "GetPlateInfo_w38", "st": 300, "c": "DailyLimitResumption", "Index": 0, "apiv": "w42"}},
    {"target": "longhu_quote", "action": "DailyLimitPerformance2", "controller": "HomeDingPan", "params": {"Order": 1, "a": "DailyLimitPerformance2", "st": 300, "apiv": "w40", "Type": 5, "c": "HomeDingPan", "Index": 0, "PidType": 1}},
)


async def sync(
    trade_date: date, *,
    run_public_blocking: Callable[..., Awaitable[Any]],
    persist: Callable[[str, str, list[dict[str, Any]]], Awaitable[int]],
    source_factory: Callable[[], Any],
) -> dict[str, Any]:
    observed_at = datetime.now(timezone.utc)
    result: dict[str, Any] = {"status": "completed", "trade_date": str(trade_date), "capabilities": {}, "stored": 0, "research_only": True, "live_effect": "none"}
    source = source_factory()
    for item in SUPPLEMENTAL_REQUESTS:
        action = str(item["action"])
        capability = f"longhu:{action}"
        request = {"target": item["target"], "params": dict(item["params"])}
        try:
            envelope = await run_public_blocking(source.raw_call, request, timeout_seconds=90)
            pages = envelope.get("pages") if isinstance(envelope, dict) else []
            stored = 0
            for page in pages if isinstance(pages, list) else []:
                payload = page.get("payload") if isinstance(page, dict) else None
                if not isinstance(payload, dict):
                    continue
                rows = normalize_payload(target=str(item["target"]), action=action, controller=str(item["controller"]), payload=payload, trade_date=trade_date, observed_at=observed_at)
                stored += await persist("longhuvip", capability, rows)
            result["capabilities"][action] = {"status": "completed", "pages": len(pages) if isinstance(pages, list) else 0, "stored": stored}
            result["stored"] += stored
        except Exception as error:  # noqa: BLE001 - one capability must not block the close review
            result["status"] = "partial"
            result["capabilities"][action] = {"status": "failed", "reason": f"{type(error).__name__}: {str(error)[:180]}"}
    return result


__all__ = ["SUPPLEMENTAL_REQUESTS", "sync"]
