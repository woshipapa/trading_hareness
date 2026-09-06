"""Longhu morning-auction evidence capture for the next-session research ledger."""

from __future__ import annotations

from datetime import datetime, time
from typing import Any, Awaitable, Callable, Mapping
from zoneinfo import ZoneInfo

from .longhu_research_features import payload_rows, strict_symbol


CN_TZ = ZoneInfo("Asia/Shanghai")
MORNING_AUCTION_REQUEST = {
    "target": "longhu_quote",
    "params": {
        "Order": 1, "a": "MorningBiddingList", "st": 300, "c": "HomeDingPan",
        "Index": 0, "PidType": 0, "apiv": "w41", "Type": 4,
    },
}


def capture_window(observed_at: datetime) -> bool:
    local = observed_at.astimezone(CN_TZ)
    return local.weekday() < 5 and time(9, 25) <= local.time() <= time(9, 30)


def _symbol(value: Any) -> str | None:
    if isinstance(value, Mapping):
        for key in ("ts_code", "symbol", "code", "StockID", "stock_id"):
            result = strict_symbol(value.get(key))
            if result:
                return result
    if isinstance(value, list):
        for item in value[:4]:
            result = strict_symbol(item)
            if result:
                return result
    return None


def normalize(payload: Mapping[str, Any], observed_at: datetime) -> list[dict[str, Any]]:
    """Create explicit auction evidence rows; fields remain provider raw."""
    exchange_day = observed_at.astimezone(CN_TZ).date().isoformat()
    events: list[dict[str, Any]] = []
    for item in payload_rows(payload):
        symbol = _symbol(item)
        if not symbol:
            continue
        events.append({
            "ts_code": symbol,
            "event_type": "longhu_morning_auction",
            "published_at": observed_at.isoformat(),
            "title": f"Longhu 集合竞价：{symbol}",
            "event_identity_key": f"longhuvip:morning_auction:{symbol}:{exchange_day}",
            "raw": {
                "target": "longhu_quote", "action": "MorningBiddingList",
                "availability_basis": "auction_window_gateway_receipt",
                "research_only": True, "replay_only": True, "live_effect": "none",
                "row": item,
            },
        })
    return events


async def capture(
    observed_at: datetime, *, call: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]],
    persist: Callable[[str, list[dict[str, Any]]], Awaitable[int]],
) -> dict[str, Any]:
    if not capture_window(observed_at):
        return {"status": "skipped", "reason": "outside_morning_auction_window", "stored": 0}
    try:
        result = await call(dict(MORNING_AUCTION_REQUEST))
        pages = result.get("pages") if isinstance(result, dict) else []
        events: list[dict[str, Any]] = []
        for page in pages if isinstance(pages, list) else []:
            payload = page.get("payload") if isinstance(page, Mapping) else None
            if isinstance(payload, Mapping):
                events.extend(normalize(payload, observed_at))
        stored = await persist("longhuvip", events) if events else 0
        return {"status": "completed", "pages": len(pages) if isinstance(pages, list) else 0,
                "received": len(events), "stored": stored, "research_only": True, "live_effect": "none"}
    except Exception as error:  # noqa: BLE001 - scheduler retries next cadence
        return {"status": "failed", "stored": 0, "reason": f"{type(error).__name__}: {str(error)[:180]}"}


__all__ = ["MORNING_AUCTION_REQUEST", "capture", "capture_window", "normalize"]
