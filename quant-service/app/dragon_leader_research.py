"""Research-only, post-close projection for A-share limit-up leaders.

The module intentionally separates what the final limit pool can establish
from what requires next-session auction, order-book and minute evidence.  It
does not produce orders, prices, or an expected return.
"""

from __future__ import annotations

from typing import Any


MODEL_VERSION = "dragon-leader-v1"
SCORE_MODEL_VERSION = "dragon-leader-score-v1"


def _number(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed == parsed else None


def _streak(item: dict[str, Any]) -> int:
    watch = item.get("continuation_watch") or {}
    return int(watch.get("streak_count") or item.get("board_count") or item.get("streak_count") or 0)


def _symbol(item: dict[str, Any]) -> str:
    return str(item.get("ts_code") or item.get("symbol") or "").upper()


def _leader_sort_key(item: dict[str, Any]) -> tuple[float, float, float, str]:
    watch = item.get("continuation_watch") or {}
    return (
        -float(_streak(item)),
        -float(watch.get("seal_to_float") or 0),
        -float(_number(item.get("limit_amount")) or 0),
        _symbol(item),
    )


def dragon_leader_score(item: dict[str, Any], *, market: dict[str, Any]) -> dict[str, Any]:
    """Build a transparent, post-close leader score shadow.

    The score is intentionally partial until next-session auction and
    intraday confirmation are observed.  It is evidence for review only and
    must never be used as a live threshold or order input.
    """
    leader_rank = int((item.get("dragon_leader_watch") or {}).get("leader_rank") or 0)
    streak = _streak(item)
    highest = int(market.get("highest_observed_streak") or 0)
    ladder_count = int(market.get("observable_multi_board_count") or 0)
    board = item.get("board_context") or {}
    continuation = item.get("continuation_watch") or {}
    daily = item.get("daily_features") or {}
    limit_context = item.get("limit_context") or {}
    lhb = item.get("lhb_context") or {}
    components: dict[str, dict[str, Any]] = {}
    reasons: list[str] = []

    # The available post-close market proxy is deliberately capped below the
    # full M=20 definition; promotion/repair/negative-feedback inputs are not
    # inferred when the corresponding pool is absent.
    market_score = min(8.0, float(highest) if highest else 0.0)
    components["market_sentiment"] = {"score": market_score, "max_score": 8.0, "status": "partial", "missing": [
        "promotion_rate", "high_level_negative_feedback", "board_repair_rate",
    ]}

    if leader_rank == 1:
        leader_score = 20.0
        reasons.append("观察梯队最高板")
    elif leader_rank > 1:
        leader_score = max(4.0, 16.0 - (leader_rank - 2) * 3.0)
        reasons.append(f"观察梯队第{leader_rank}位")
    else:
        leader_score = 0.0
    components["leader_status"] = {"score": leader_score, "max_score": 20.0, "status": "observed" if leader_rank else "unavailable"}

    if board.get("exact_member_mapping"):
        theme_members = int((item.get("dragon_leader_watch") or {}).get("theme_context", {}).get("observable_limit_up_count") or 0)
        theme_ladders = int((item.get("dragon_leader_watch") or {}).get("theme_context", {}).get("observable_multi_board_count") or 0)
        flow_percentile = _number(board.get("flow_percentile"))
        theme_score = min(15.0, (5.0 if theme_members >= 3 else 2.0) + (4.0 if theme_ladders >= 2 else 0.0) + (6.0 * max(0.0, min(1.0, flow_percentile or 0.0))))
        reasons.append(f"精确板块映射，观察成员{theme_members}只")
        components["theme_strength"] = {"score": round(theme_score, 2), "max_score": 15.0, "status": "observed", "members": theme_members, "multi_board_members": theme_ladders}
    else:
        components["theme_strength"] = {"score": 0.0, "max_score": 15.0, "status": "unavailable", "missing": ["exact_membership"]}

    turnover = _number(limit_context.get("turnover_rate"))
    volume_multiple = _number(daily.get("volume_multiple_5d"))
    chip_score = 0.0
    if turnover is not None:
        chip_score += min(10.0, turnover / 4.0)
    if volume_multiple is not None:
        chip_score += min(10.0, max(0.0, volume_multiple - 0.5) * 5.0)
    components["chip_structure"] = {"score": round(chip_score, 2), "max_score": 20.0,
                                     "status": "partial" if turnover is None or volume_multiple is None else "observed",
                                     "turnover_rate": turnover, "volume_multiple_5d": volume_multiple,
                                     "missing": [name for name, value in (("turnover_rate", turnover), ("volume_multiple_5d", volume_multiple)) if value is None]}

    components["intraday_confirmation"] = {"score": None, "max_score": 15.0, "status": "not_observed",
                                            "missing": ["opening_auction", "first_minutes_acceptance", "theme_relative_strength"]}

    risk_penalty = 0.0
    risk_flags: list[str] = []
    if str(limit_context.get("status") or item.get("status") or "") == "一字板":
        risk_penalty += 4.0
        risk_flags.append("one_word_board")
    if turnover is not None and turnover >= 35:
        risk_penalty += 3.0
        risk_flags.append("extreme_turnover")
    institution_net_buy = _number(lhb.get("institution_net_buy"))
    if institution_net_buy is not None and institution_net_buy < 0:
        risk_penalty += 3.0
        risk_flags.append("lhb_institution_net_sell")
    components["risk_penalty"] = {"score": round(risk_penalty, 2), "max_penalty": 10.0, "status": "observed"}

    available_score = market_score + leader_score + components["theme_strength"]["score"] + chip_score - risk_penalty
    available_max = 8.0 + 20.0 + 15.0 + 20.0
    return {
        "model_version": SCORE_MODEL_VERSION,
        "status": "partial_shadow",
        "live_effect": "none",
        "score": round(max(0.0, available_score), 2),
        "max_available_score": available_max,
        "coverage_ratio": round((8.0 + 20.0 + (15.0 if components["theme_strength"]["status"] == "observed" else 0.0) + (20.0 if components["chip_structure"]["status"] == "observed" else 0.0)) / 78.0, 3),
        "components": components,
        "reasons": reasons,
        "risk_flags": risk_flags,
        "notice": "盘后部分证据影子评分；竞价、盘中承接和板块相对强度未取得前，不是买入信号。",
    }


def enrich_dragon_leader_watches(items: list[dict[str, Any]]) -> dict[str, Any]:
    """Attach transparent leader context to an already persisted limit-up pool.

    ``items`` is mutated only by adding ``dragon_leader_watch``.  The returned
    market context is deliberately marked partial: it is derived from the
    locally observed limit-up union, not an exchange-wide live tape.
    """
    observable = [item for item in items if _symbol(item)]
    ladders = sorted((item for item in observable if _streak(item) >= 2), key=_leader_sort_key)
    max_streak = max((_streak(item) for item in observable), default=0)
    if not observable:
        market_state = "unavailable"
    elif not ladders:
        market_state = "first_board_dominated"
    elif len(ladders) >= 5:
        market_state = "ladder_breadth_observed"
    else:
        market_state = "thin_ladder_observed"
    market = {
        "model_version": MODEL_VERSION,
        "status": "partial_post_close_limit_up_union" if observable else "unavailable",
        "market_state": market_state,
        "observable_limit_up_count": len(observable),
        "observable_multi_board_count": len(ladders),
        "highest_observed_streak": max_streak,
        "coverage_flags": [
            "limit_up_pool_union_only",
            "limit_down_and_open_board_breadth_not_complete",
            "not_exchange_official_full_market_coverage",
        ],
        "interpretation": "仅描述已落库涨停池中的梯队广度；不能替代涨跌停、炸板率和全市场实时情绪。",
    }
    leader_ranks = {_symbol(item): rank for rank, item in enumerate(ladders, start=1)}
    themes: dict[str, list[dict[str, Any]]] = {}
    for item in observable:
        board = item.get("board_context") or {}
        sector_key = str(board.get("sector_key") or "")
        if board.get("exact_member_mapping") and sector_key:
            themes.setdefault(sector_key, []).append(item)

    for item in observable:
        symbol = _symbol(item)
        continuation = item.get("continuation_watch") or {}
        board = item.get("board_context") or {}
        sector_key = str(board.get("sector_key") or "")
        theme_members = themes.get(sector_key, []) if board.get("exact_member_mapping") and sector_key else []
        theme_ladders = [member for member in theme_members if _streak(member) >= 2]
        theme_context = {
            "status": "observed" if theme_members else "unavailable",
            "sector_key": sector_key or None,
            "label": board.get("label"),
            "observable_limit_up_count": len(theme_members),
            "observable_multi_board_count": len(theme_ladders),
            "net_amount": _number(board.get("net_amount")),
            "exact_member_mapping": bool(board.get("exact_member_mapping")),
        }
        risk_flags = [
            "post_close_only",
            "next_session_manual_review",
            "no_automatic_order",
            "market_breadth_partial_limit_up_only",
            "auction_not_observed",
            "order_book_seal_erosion_not_observed",
        ]
        if not theme_members:
            risk_flags.append("exact_theme_ladder_unavailable")
        if str(item.get("status") or "") == "一字板":
            risk_flags.append("one_word_board_not_entry")
        risk_flags = list(dict.fromkeys([*risk_flags, *(continuation.get("risk_flags") or [])]))
        eligible = bool(continuation.get("eligible"))
        if not eligible:
            tier = "not_qualified"
        elif len(theme_ladders) >= 2 and (_number(board.get("net_amount")) or 0) > 0:
            tier = "theme_ladder_manual_review"
        elif leader_ranks.get(symbol) is not None:
            tier = "ladder_manual_review"
        else:
            tier = "continuation_manual_review"
        item["dragon_leader_watch"] = {
            "model_version": MODEL_VERSION,
            "status": "candidate" if eligible else "filtered",
            "eligible": eligible,
            "leader_rank": leader_ranks.get(symbol),
            "streak_count": _streak(item),
            "market_context": market,
            "theme_context": theme_context,
            "review_tier": tier,
            "session_confirmation": {
                "status": "not_observed",
                "required": ["next_session_tradability", "opening_auction", "first_minutes_acceptance", "theme_and_market_confirmation"],
                "policy": "盘后候选不是盘中追板，也不是订单。",
            },
            "risk_flags": risk_flags,
            "interpretation": "龙头仅是已观察梯队中的相对位置；需在下一交易日以可成交性和实时承接证伪或确认。",
        }
        item["dragon_leader_watch"]["score_shadow"] = dragon_leader_score(item, market=market)
    return market


def rank_dragon_leader_candidates(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return only eligible manual-review watches in deterministic ladder order."""
    eligible = [item for item in items if bool((item.get("dragon_leader_watch") or {}).get("eligible"))]
    eligible.sort(key=lambda item: (
        int((item.get("dragon_leader_watch") or {}).get("leader_rank") or 10_000),
        _leader_sort_key(item),
    ))
    return [{**item, "dragon_leader_watch": {**dict(item["dragon_leader_watch"]), "rank": rank}}
            for rank, item in enumerate(eligible, start=1)]


__all__ = ["MODEL_VERSION", "SCORE_MODEL_VERSION", "dragon_leader_score", "enrich_dragon_leader_watches", "rank_dragon_leader_candidates"]
