"""Declarative ownership contracts for background research tasks.

The registry is intentionally metadata-only: factories remain in the
composition root, while profile ownership, evidence outputs and operational
expectations have one machine-readable source of truth.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final


@dataclass(frozen=True)
class RuntimeTaskContract:
    label: str
    owner_profile: str
    cadence: str
    provider_capabilities: tuple[str, ...]
    evidence_datasets: tuple[str, ...]
    description: str
    freshness_budget_seconds: float | None = None


RUNTIME_TASK_CONTRACTS: Final[dict[str, RuntimeTaskContract]] = {
    "intraday_monitor": RuntimeTaskContract(
        "intraday_monitor", "intraday_edge", "30s during session", ("order_book_quote", "a_share_prices_snapshot"),
        ("intraday_scan_runs", "intraday_signal_events"), "bounded watchlist scan and research alert writer", 90,
    ),
    "super_get_fast_quote": RuntimeTaskContract(
        "super_get_fast_quote", "intraday_edge", "1s rotating during session", ("rt_k",),
        ("intraday_fast_quotes",), "secondary same-session quote confirmation", 30,
    ),
    "minute_profile_capture": RuntimeTaskContract(
        "minute_profile_capture", "intraday_edge", "bounded market-session polling", ("intraday_minutes",),
        ("intraday_minute_sessions",), "minute features and time-of-day profile evidence", 180,
    ),
    "tencent_order_book": RuntimeTaskContract(
        "tencent_order_book", "intraday_edge", "3s during session", ("order_book_quote",),
        ("intraday_order_book_observations",), "bounded watchlist order-book capture", 20,
    ),
    "board_flow_curve": RuntimeTaskContract(
        "board_flow_curve", "intraday_edge", "market-session polling", ("board_flow",),
        ("intraday_board_flow_snapshots",), "board-flow curve evidence", 180,
    ),
    "market_event_capture": RuntimeTaskContract(
        "market_event_capture", "intraday_edge", "60s during market observation windows",
        ("a_share_auction_snapshot", "a_share_limit_up_pool", "a_share_limit_break_pool", "a_share_limit_down_pool",
         "a_share_limit_up_ladder", "a_share_hot_stock_list", "a_share_skyrocket_list",
         "a_share_anomaly_analysis_list", "a_share_auction_short_term_benchmark"),
        ("market_events", "raw_market_observations"),
        "all-A auction, limit-pool, limit-chain and THS attention evidence capture", 180,
    ),
    "auction_pulse": RuntimeTaskContract(
        "auction_pulse", "intraday_edge", "2s during 09:15-09:30 Shanghai opening auction",
        ("longhu:MorningBiddingList", "longhu:GetBKJJ_W36", "longhu:GetPlateInfo_w38", "longhu:RiseFallAnalysis", "longhu:MoodNumCount"),
        ("raw_market_observations",),
        "bounded Longhu auction, sector anomaly, sentiment and money-flow evidence with cooled Feishu alerts",
        10,
    ),
    "public_evidence_capture": RuntimeTaskContract(
        "public_evidence_capture", "intraday_edge", "30s tick; per-source 90s-15min cadences",
        ("news_flash", "investor_qa", "stock_change", "hot_rank_popularity", "hot_rank_surge",
         "board_change_snapshot", "market_sentiment_snapshot"),
        ("market_events", "raw_market_observations"),
        "token-free news, investor Q&A, anomaly tape, attention ranks and self-computed sentiment", 900,
    ),
    "post_close_public_archive": RuntimeTaskContract(
        "post_close_public_archive", "research", "once per trading day, 15:10-23:30 windows",
        ("limit_pool", "stock_change_daily_summary", "market_sentiment_close", "a_share_hot_stock_list_history",
         "a_share_dragon_tiger_list", "eastmoney_datacenter", "margin_market", "a_share_valuations_snapshot",
         "ths_index_prices_snapshot", "tick_flow_daily", "capital_changes"),
        ("market_events", "raw_market_observations"),
        "archive short-lived and post-close public evidence (pools, LHB, corporate events, ticks)",
    ),
    "storage_tiering_mover": RuntimeTaskContract(
        "storage_tiering_mover", "research", "bounded passes outside 09:00-15:45 on trading days", (),
        ("raw_market_observations", "intraday_quote_observations", "intraday_rule_input_snapshots"),
        "copy closed sessions of these hot tables into their stock_cold twins (*_cold), delete hot rows past the "
        "hot window only when the cold copy matches; inert until the owner grants SELECT,INSERT on the twins",
    ),
    "peer_close_research": RuntimeTaskContract(
        "peer_close_research", "research", "once per trading date after 16:15 (catch-up before 09:00)",
        ("intraday_minutes",),
        ("raw_market_observations", "xiaojie_leader_flow_observations", "automation_runs"),
        "the peer-owned close stages the owner's pipeline does not run: teacher plan roll (settle, promote, "
        "observe, retire), watch-list daily review and 小杰 outcome settlement, each behind its receipt",
    ),
    "all_a_level1_snapshot": RuntimeTaskContract(
        "all_a_level1_snapshot", "intraday_edge", "60s during evidence observation session from 09:15",
        ("a_share_prices_snapshot",), ("raw_market_observations",),
        "complete all-A Level-1 raw snapshot for width/rank and validation windows", 120,
    ),
    "strategy_review": RuntimeTaskContract(
        "strategy_review", "research", "scheduled checkpoints", ("daily", "board_flow"),
        ("analyst_market_reviews", "strategy_reviews"), "post-close descriptive strategy review",
    ),
    "post_close_strategy": RuntimeTaskContract(
        "post_close_strategy", "research", "same-date post-close window", ("daily", "daily_basic"),
        ("strategy_candidates", "automation_runs"), "post-close research candidate generation",
    ),
    "ten_day_leader_rotation": RuntimeTaskContract(
        "ten_day_leader_rotation", "research", "scheduled post-close", ("daily", "limit_list_d"),
        ("ten_day_leader_rotation_runs",), "ten-day leader rotation research",
    ),
    "daily_strategy_summary": RuntimeTaskContract(
        "daily_strategy_summary", "research", "daily", (),
        ("strategy_day_summaries",), "research-only daily summary materialization",
    ),
    "ths_member_backfill": RuntimeTaskContract(
        "ths_member_backfill", "research", "bounded background batches", ("ths_member",),
        ("sector_membership_history",), "point-in-time THS constituent backfill",
    ),
    "all_board_member_backfill": RuntimeTaskContract(
        "all_board_member_backfill", "research", "bounded background batches", ("dc_member", "ths_member"),
        ("sector_membership_history",), "bounded board member coverage backfill",
    ),
}


def runtime_task_contract(label: str) -> RuntimeTaskContract:
    try:
        return RUNTIME_TASK_CONTRACTS[label]
    except KeyError as error:
        raise ValueError(f"unknown runtime task contract: {label}") from error


def runtime_profile_owns_task(profile: str, label: str) -> bool:
    """Return whether a declared runtime profile may acquire a task lease."""
    if profile == "full":
        return True
    return runtime_task_contract(label).owner_profile == profile


def runtime_task_contract_catalog() -> list[dict[str, Any]]:
    """Expose deterministic, secret-free runtime ownership to agents/UI."""
    return [
        {
            "label": item.label,
            "owner_profile": item.owner_profile,
            "cadence": item.cadence,
            "provider_capabilities": list(item.provider_capabilities),
            "evidence_datasets": list(item.evidence_datasets),
            "description": item.description,
            "freshness_budget_seconds": item.freshness_budget_seconds,
        }
        for item in sorted(RUNTIME_TASK_CONTRACTS.values(), key=lambda item: item.label)
    ]


def intraday_edge_task_labels() -> frozenset[str]:
    return frozenset(
        item.label for item in RUNTIME_TASK_CONTRACTS.values() if item.owner_profile == "intraday_edge"
    )


__all__ = [
    "RUNTIME_TASK_CONTRACTS", "RuntimeTaskContract", "intraday_edge_task_labels",
    "runtime_profile_owns_task", "runtime_task_contract", "runtime_task_contract_catalog",
]
