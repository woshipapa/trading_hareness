"""What each registered strategy needs from the data layer, by capability.

A strategy states *capabilities* ("limits.prices", "sector.membership"),
never vendors; which source serves them is the data-source catalog's job.
``legacy_couplings`` lists any vendor name still hard-coded in strategy code
(file:line), so it stays visible until migrated to a capability read.  The
seven found in the 2026-09-18 audit were migrated the same day; a test keeps
new vendor literals out of strategy modules.
"""

from __future__ import annotations

from typing import Any, Final

from ..datasources.contracts import CapabilityRequirement as Need
from ..datasources.contracts import StrategyDataNeeds


def _needs(strategy: str, *needs: Need, legacy: tuple[str, ...] = ()) -> StrategyDataNeeds:
    return StrategyDataNeeds(strategy, tuple(needs), legacy)


STRATEGY_DATA_NEEDS: Final[dict[str, StrategyDataNeeds]] = {item.strategy: item for item in (
    _needs(
        "intraday_watchlist_confirmation",
        Need("quote.watch_snapshot", purpose="price/volume ratio/turnover per scan"),
        Need("quote.order_book", False, "OFI and seal depth on the priority subset"),
        Need("bars.minute", purpose="minute confirmation and profile"),
        Need("quote.all_a_snapshot", purpose="exact-peer breadth"),
        Need("sector.membership", purpose="exact peer sets and paper sector exposure; qualification lists "
                                            "(catalog.NON_SECTOR_GROUPS) are not sectors",
             taxonomies=("ths_concept_flow", "ths_index_n", "ths_industry")),
        Need("limits.prices", purpose="limit-aware rules"),
        Need("flow.watch_intraday", False, "research-only flow confirmation"),
    ),
    _needs("watchlist_main_wave_shadow", Need("bars.daily"), Need("fundamentals.daily_basic")),
    _needs(
        "countertrend_rebound_shadow",
        Need("bars.daily"), Need("bars.adjustment_factor"),
        Need("sector.membership", False, "technology-industry universe", taxonomies=("ths_industry",)),
        Need("quote.watch_snapshot", False, "next-session acceptance"),
    ),
    _needs(
        "ten_day_leader_rotation_shadow",
        Need("bars.daily"), Need("limits.limit_up_pool", purpose="leader universe"),
        Need("sector.membership", purpose="intraday peers via the watchlist loader, same exclusions"),
        Need("bars.minute", False, "VWAP coordination observation"),
    ),
    _needs(
        "post_close_base_candidates",
        Need("bars.daily"), Need("bars.adjustment_factor", purpose="30-session structure window"),
        Need("fundamentals.daily_basic"),
        Need("flow.stock_daily", False, "main net flow context"),
    ),
    _needs(
        "disclosure_day_watch",
        Need("events.disclosure_schedule"), Need("events.earnings_forecast"), Need("events.earnings_express"),
        Need("reference.trade_calendar"),
    ),
    _needs("limit_up_continuation", Need("bars.daily"), Need("limits.prices")),
    _needs(
        "teacher_review_playbooks",
        Need("quote.watch_snapshot", purpose="per-scan price, turnover, volume ratio and cumulative amount"),
        Need("quote.order_book", False, "sealed bid-only book at the limit price"),
        Need("bars.minute", False, "30/60-minute two-leg divergence (period K-line history + forming bar)"),
        Need("bars.daily", purpose="pre-session MA/platform/prior-high plan and session settlement"),
        Need("limits.limit_up_pool", False, "post-close forecast settlement (first seal time, plate counts)"),
        Need("reference.trade_calendar", purpose="point-in-time first eligible session"),
    ),
    _needs(
        "post_close_limit_lift_pattern",
        Need("limits.limit_up_pool"), Need("limits.ladder"), Need("bars.minute", purpose="minute-pattern replay"),
        Need("bars.daily"),
    ),
    _needs(
        "xiaojie_leader_flow",
        Need("quote.all_a_snapshot", purpose="all-A cross-section"), Need("limits.prices", purpose="blocked without today's limits"),
        # Order, not coverage: an industry map gives a name one sector, a
        # concept map gives it twenty-three, and "is this name leading its
        # sector" only has an answer under the first.
        Need("sector.membership", purpose="sector core confirmation; first loaded taxonomy in order wins",
             taxonomies=("longhu_ths_industry", "ths_concept_flow")),
        Need("bars.daily", purpose="MA20/overheat gate"),
    ),
    _needs(
        "launch_radar",
        Need("quote.all_a_snapshot", purpose="cross-section price and cumulative volume per scan"),
        Need("limits.prices", purpose="limit price for the launch band and sealed anchors"),
        Need("sector.membership", purpose="a sealed anchor in a shared concept", taxonomies=("ths_concept_flow",)),
        Need("bars.daily", purpose="the name's own 5-day volume baseline"),
    ),
    _needs(
        "longhu_multifactor_shadow",
        Need("quote.watch_snapshot", purpose="quote return and volume ratio"),
        Need("bars.minute", purpose="minute momentum"),
        Need("quote.order_book", purpose="order-book imbalance"),
        Need("flow.watch_intraday", False, "large-order net ratio"),
        Need("sector.flow_curve", False, "board-relative strength"),
        Need("auction.open_snapshot", False, "opening-auction premium"),
    ),
)}


def strategy_taxonomies(strategy: str) -> tuple[str, ...]:
    """The sector taxonomies ``strategy`` reads membership from, in order."""
    for need in STRATEGY_DATA_NEEDS[strategy].needs:
        if need.capability == "sector.membership" and need.taxonomies:
            return need.taxonomies
    raise LookupError(f"{strategy} declares no sector taxonomies")


def strategy_data_needs_catalog() -> list[dict[str, Any]]:
    return [{
        "strategy": item.strategy,
        "needs": [{"capability": need.capability, "required": need.required, "purpose": need.purpose,
                   "taxonomies": list(need.taxonomies)} for need in item.needs],
        "legacy_couplings": list(item.legacy_couplings),
    } for item in sorted(STRATEGY_DATA_NEEDS.values(), key=lambda value: value.strategy)]


__all__ = ["STRATEGY_DATA_NEEDS", "strategy_data_needs_catalog", "strategy_taxonomies"]
