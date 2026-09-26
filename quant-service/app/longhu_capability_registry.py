"""Explicit integration map for every documented Longhu operation.

The authenticated gateway already exposes the vendor operations.  This module
adds the missing application contract: which operations are used by the live
watch path, which are bounded candidate confirmations, and which remain
research/post-close evidence until their field and point-in-time semantics are
validated.  It deliberately does not grant any operation live decision power.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final

from .licensed_stock_api import DOCUMENTED_OPERATIONS, documented_examples


LIVE_ACTIONS: Final[frozenset[str]] = frozenset({
    "GetStockPanKou",
    "GetStockTrendIncremental",
})

CANDIDATE_CONFIRMATION_ACTIONS: Final[frozenset[str]] = frozenset({
    "GetStockDaDanTrendIncremental",
})

RESEARCH_ACTIONS: Final[frozenset[str]] = frozenset({
    "GetStockBid",
    "GetBKJJ_W36",
    "GetBKJJBL",
    "GetZhangTingGene",
    "StockChouMaByTimeNew_W5",
    "GetStockChouMa_New",
    "GetMainMonitor_w30",
    "GetWeiTuo_W14",
    "Radar",
    "GetHotPHB",
    "ChangeStatistics",
    "GroupCount_w28",
    "GetPianLiZhi_Index",
    "GlobalCommon",
    "GetPlate_Info_QJ",
    "GetStockIDPlate",
})

POST_CLOSE_ACTIONS: Final[frozenset[str]] = frozenset({
    "MorningBiddingList",
    "DailyLimitPerformance",
    "DailyLimitPerformance2",
    "RiseFallAnalysis",
    "MoodNumCount",
    "GetPlateInfo_w38",
    "GetTopList",
    "GetList",
    "InfoList",
})


@dataclass(frozen=True)
class LonghuCapabilityContract:
    target: str
    action: str
    controller: str
    integration: str
    roles: tuple[str, ...]
    strategy_eligible: bool
    availability: str
    evidence_dataset: str = "raw_market_observations"


def _integration(target: str, action: str) -> tuple[str, tuple[str, ...], bool, str]:
    if action in LIVE_ACTIONS:
        if action == "GetStockPanKou":
            return "live_watch", ("quote", "order_book"), True, "exchange_timestamp_required"
        return "live_watch", ("minute", "surge_context"), True, "exchange_date_required"
    if action in CANDIDATE_CONFIRMATION_ACTIONS:
        return "candidate_confirmation", ("large_order_flow",), False, "exchange_timestamp_required"
    if action in POST_CLOSE_ACTIONS:
        return "post_close_research", ("market_context", "replay"), False, "owner_gateway_receipt"
    if target == "longhu_history" or action in {"GetKLineDay_W14", "GetPMSL_KQXY"}:
        return "historical_research", ("historical_context", "replay"), False, "historical_available_at"
    return "shadow_research", ("research_factor", "replay"), False, "provider_available_at"


def capability_contracts() -> tuple[LonghuCapabilityContract, ...]:
    """Return one deterministic contract per documented target/action pair."""
    examples = {
        (str(item.get("target")), str(item.get("action"))): item
        for item in documented_examples()
        if item.get("target", "").startswith("longhu_") and item.get("action")
    }
    rows: list[LonghuCapabilityContract] = []
    seen: set[tuple[str, str]] = set()
    for item in DOCUMENTED_OPERATIONS:
        target, action = str(item["target"]), str(item["action"])
        key = (target, action)
        if not target.startswith("longhu_") or key in seen:
            continue
        seen.add(key)
        example = examples.get(key, {})
        integration, roles, eligible, availability = _integration(target, action)
        rows.append(LonghuCapabilityContract(
            target=target,
            action=action,
            controller=str(item.get("controller") or example.get("controller") or ""),
            integration=integration,
            roles=roles,
            strategy_eligible=eligible,
            availability=availability,
        ))
    return tuple(sorted(rows, key=lambda row: (row.target, row.action)))


def capability_contract_catalog() -> list[dict[str, Any]]:
    return [
        {
            "target": row.target,
            "action": row.action,
            "controller": row.controller,
            "integration": row.integration,
            "roles": list(row.roles),
            "strategy_eligible": row.strategy_eligible,
            "availability": row.availability,
            "evidence_dataset": row.evidence_dataset,
            "research_only": True,
            "live_effect": "none",
        }
        for row in capability_contracts()
    ]


__all__ = [
    "CANDIDATE_CONFIRMATION_ACTIONS",
    "LIVE_ACTIONS",
    "POST_CLOSE_ACTIONS",
    "RESEARCH_ACTIONS",
    "LonghuCapabilityContract",
    "capability_contract_catalog",
    "capability_contracts",
]
