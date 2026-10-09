"""Bounded post-close Longhu supplemental evidence capture."""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Awaitable, Callable

from .longhu_research_features import normalize_payload


BASE_REQUESTS: tuple[dict[str, Any], ...] = (
    {"target": "longhu_market_wide", "action": "RiseFallAnalysis", "controller": "HomeDingPan", "params": {"a": "RiseFallAnalysis", "apiv": "w43", "c": "HomeDingPan", "PhoneOSNew": 1, "VerSion": "5.22.0.2"}},
    {"target": "longhu_market_wide", "action": "MoodNumCount", "controller": "MarketMood", "params": {"a": "MoodNumCount", "apiv": "w43", "c": "MarketMood", "PhoneOSNew": 1, "VerSion": "5.22.0.2"}},
    {"target": "longhu_market_wide", "action": "GetPlateInfo_w38", "controller": "DailyLimitResumption", "params": {"a": "GetPlateInfo_w38", "st": 300, "c": "DailyLimitResumption", "Index": 0, "apiv": "w42"}},
    {"target": "longhu_quote", "action": "DailyLimitPerformance2", "controller": "HomeDingPan", "params": {"Order": 1, "a": "DailyLimitPerformance2", "st": 300, "apiv": "w40", "Type": 5, "c": "HomeDingPan", "Index": 0, "PidType": 1}},
)


def supplemental_requests(trade_date: date) -> tuple[dict[str, Any], ...]:
    """Build only post-close and next-session evidence requests for one date."""
    day = trade_date.isoformat()
    history: list[dict[str, Any]] = [{
        "target": "longhu_history", "action": "MorningBiddingList", "controller": "HisHomeDingPan",
        "availability_basis": "post_close_history_replay", "next_session_only": True,
        "params": {"Order": 1, "a": "MorningBiddingList", "st": 60, "c": "HisHomeDingPan", "Index": 0, "PidType": 0, "Date": day, "apiv": "w41", "Type": 4},
    }]
    for action, version, kind in (
        ("DailyLimitPerformance", "w31", 4),
        ("DailyLimitPerformance2", "w42", 5),
    ):
        for pid_type in range(1, 6):
            history.append({
                "target": "longhu_history", "action": action, "controller": "HisHomeDingPan",
                "availability_basis": "post_close_history_replay", "next_session_only": True,
                "params": {"Order": 1, "a": action, "st": 300, "c": "HisHomeDingPan", "Index": 0, "PidType": pid_type, "Day": day, "apiv": version, "Type": kind},
            })
    next_session_context = (
        {"target": "longhu_article", "action": "GetTopList", "controller": "PCNewsFlash", "availability_basis": "post_close_article_receipt", "next_session_only": True,
         "params": {"a": "GetTopList", "st": 20, "apiv": "w44", "c": "PCNewsFlash", "Index": 0}},
        {"target": "longhu_article", "action": "GetList", "controller": "PCNewsFlash", "availability_basis": "post_close_article_receipt", "next_session_only": True,
         "params": {"a": "GetList", "st": 100, "c": "PCNewsFlash", "Index": 0, "apiv": "w44", "Type": 0}},
        {"target": "longhu_lhb", "action": "InfoList", "controller": "Topic", "availability_basis": "post_close_lhb_topic_receipt", "next_session_only": True,
         "params": {"a": "InfoList", "st": 100, "apiv": "w44", "c": "Topic", "Index": 0}},
    )
    # 选股宝's public pools through the same gateway: the last seal time, the
    # breaks and N天M板 that the Longhu review does not carry.
    public_pools = tuple(
        {"target": "xuangubao", "action": f"pool:{pool}", "controller": "xuangubao",
         "availability_basis": "post_close_public_pool_receipt", "params": {"pool_name": pool, "date": day}}
        for pool in ("limit_up", "limit_up_broken", "limit_down")
    )
    return (*BASE_REQUESTS, *history, *next_session_context, *public_pools)


async def sync(
    trade_date: date, *,
    run_public_blocking: Callable[..., Awaitable[Any]],
    persist: Callable[[str, str, list[dict[str, Any]]], Awaitable[int]],
    source_factory: Callable[[], Any],
) -> dict[str, Any]:
    observed_at = datetime.now(timezone.utc)
    result: dict[str, Any] = {"status": "completed", "trade_date": str(trade_date), "capabilities": {}, "stored": 0, "research_only": True, "live_effect": "none"}
    source = source_factory()
    for item in supplemental_requests(trade_date):
        action = str(item["action"])
        capability = f"longhu:{item['target']}:{action}"
        pid_type = item["params"].get("PidType")
        if pid_type is not None:
            capability = f"{capability}:pid{pid_type}"
        request = {"target": item["target"], "params": dict(item["params"])}
        try:
            envelope = await run_public_blocking(source.raw_call, request, timeout_seconds=90)
            pages = envelope.get("pages") if isinstance(envelope, dict) else []
            stored = 0
            for page in pages if isinstance(pages, list) else []:
                payload = page.get("payload") if isinstance(page, dict) else None
                if not isinstance(payload, dict):
                    continue
                rows = normalize_payload(
                    target=str(item["target"]), action=action, controller=str(item["controller"]),
                    payload=payload, trade_date=trade_date, observed_at=observed_at,
                    availability_basis=str(item.get("availability_basis") or "owner_gateway_receipt_post_close"),
                    next_session_only=bool(item.get("next_session_only")),
                )
                stored += await persist("longhuvip", capability, rows)
            result["capabilities"][capability] = {"status": "completed", "pages": len(pages) if isinstance(pages, list) else 0, "stored": stored}
            result["stored"] += stored
        except Exception as error:  # noqa: BLE001 - one capability must not block the close review
            result["status"] = "partial"
            result["capabilities"][capability] = {"status": "failed", "reason": f"{type(error).__name__}: {str(error)[:180]}"}
    return result


SUPPLEMENTAL_REQUESTS = BASE_REQUESTS


__all__ = ["BASE_REQUESTS", "SUPPLEMENTAL_REQUESTS", "supplemental_requests", "sync"]
