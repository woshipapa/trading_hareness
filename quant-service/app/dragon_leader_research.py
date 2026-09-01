"""Research-only, post-close projection for A-share limit-up leaders.

The module intentionally separates what the final limit pool can establish
from what requires next-session auction, order-book and minute evidence.  It
does not produce orders, prices, or an expected return.
"""

from __future__ import annotations

from typing import Any


MODEL_VERSION = "dragon-leader-v1"
SCORE_MODEL_VERSION = "dragon-leader-score-v1"
PLAYBOOK_VERSION = "dragon-leader-playbook-v1"


def _number(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed == parsed else None


def _streak(item: dict[str, Any]) -> int:
    watch = item.get("continuation_watch") or {}
    context = item.get("limit_context") or {}
    nested = context.get("continuation_watch") or {}
    return int(watch.get("streak_count") or nested.get("streak_count")
               or item.get("board_count") or context.get("board_count")
               or item.get("streak_count") or context.get("streak_count") or 0)


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


def leader_market_metrics(items: list[dict[str, Any]]) -> dict[str, Any]:
    """Derive available sentiment metrics without inventing missing data."""
    heights = [_streak(item) for item in items if _streak(item) > 0]
    current = {_symbol(item) for item in items if _symbol(item)}
    previous = [(item.get("limit_context") or {}).get("preopen_context") for item in items]
    previous = [item for item in previous if isinstance(item, dict) and _symbol(item)]
    previous_symbols = {_symbol(item) for item in previous}
    previous_by_symbol = {_symbol(item): item for item in previous}
    promotion_pairs = []
    for item in items:
        previous_item = previous_by_symbol.get(_symbol(item))
        previous_streak = _streak(previous_item) if previous_item else 0
        current_streak = _streak(item)
        if previous_item and previous_streak and current_streak:
            promotion_pairs.append(current_streak > previous_streak)
    promoted = sum(promotion_pairs) / len(promotion_pairs) if promotion_pairs else None
    repair_values = [(item.get("limit_context") or {}).get("repair_confirmed") for item in items]
    repair_values = [value for value in repair_values if isinstance(value, bool)]
    repair = sum(repair_values) / len(repair_values) if repair_values else None
    high_items = [item for item in items if _streak(item) >= 3]
    outcome_changes = [_number((item.get("limit_context") or {}).get("next_session_pct_chg")) for item in high_items]
    outcome_changes = [value for value in outcome_changes if value is not None]
    negative = sum(value < -5 for value in outcome_changes) / len(outcome_changes) if outcome_changes else None
    return {"highest_board_count": max(heights, default=None), "promotion_rate": round(promoted, 4) if promoted is not None else None, "high_level_negative_feedback_rate": round(negative, 4) if negative is not None else None, "board_repair_rate": round(repair, 4) if repair is not None else None, "sample_counts": {"current_limit_up": len(current), "previous_limit_up": len(previous_symbols), "promotion_pairs": len(promotion_pairs), "high_level": len(high_items), "repair_observed": len(repair_values), "negative_feedback_observed": len(outcome_changes)}, "quality": "observed" if all(value is not None for value in (promoted, negative, repair)) else "partial", "missing": [name for name, value in (("promotion_rate", promoted), ("high_level_negative_feedback_rate", negative), ("board_repair_rate", repair)) if value is None], "notice": "晋级率按同一代码前一交易日板数与当日板数比较；修复率需要完整开板事件，负反馈需要次日结算；缺失时保持 null。"}


def leader_playbook(item: dict[str, Any]) -> dict[str, Any]:
    """Classify three research playbooks; all remain descriptive-only."""
    streak = _streak(item)
    turnover = _number((item.get("limit_context") or {}).get("turnover_rate"))
    volume_multiple = _number((item.get("daily_features") or {}).get("volume_multiple_5d"))
    leader = item.get("dragon_leader_watch") or {}
    weak_divergence = turnover is not None and volume_multiple is not None and turnover >= 5 and volume_multiple >= 1.0
    return {"model_version": PLAYBOOK_VERSION, "status": "partial_shadow", "live_effect": "none", "plans": [
        {"strategy_key": "leader_2_to_3_selection", "eligible": streak == 2, "status": "research_candidate" if streak == 2 else "not_applicable", "missing": ["next_session_auction", "first_15m_volume_speed", "theme_relative_strength"]},
        {"strategy_key": "same_ladder_card_position", "eligible": streak >= 2 and leader.get("leader_rank") is not None, "status": "research_candidate" if streak >= 2 and leader.get("leader_rank") is not None else "not_applicable", "missing": ["expected_gap", "auction_surprise", "relative_strength"]},
        {"strategy_key": "high_level_volume_weak_divergence", "eligible": streak >= 4 and weak_divergence, "status": "research_candidate" if streak >= 4 and weak_divergence else "not_applicable", "missing": ["break_count", "break_duration", "reseal_latency"]},
    ], "next_session_confirmation_schema": next_session_confirmation(),
        "notice": "三套龙头策略只生成回放候选；未通过独立交易日/样本门禁，不进入实时阈值。"}


def seal_quality(item: dict[str, Any]) -> dict[str, Any]:
    """Score observed seal/break evidence without treating missing fields as zero."""
    context = item.get("limit_context") or {}
    fields = {
        "first_seal_time": context.get("first_seal_time"),
        "break_count": _number(context.get("break_count")),
        "break_duration_minutes": _number(context.get("break_duration_minutes")),
        "reseal_latency_minutes": _number(context.get("reseal_latency_minutes")),
        "close_seal_ratio": _number(context.get("close_seal_ratio")),
    }
    observed = [name for name, value in fields.items() if value is not None]
    if not observed:
        return {"status": "not_observed", "score": None, "max_score": 10.0, "missing": list(fields), "live_effect": "none"}
    score = 0.0
    if fields["first_seal_time"]:
        text = str(fields["first_seal_time"])
        score += 2.0 if text[:5] <= "10:00" else 1.0 if text[:5] <= "11:00" else 0.0
    if fields["break_count"] is not None:
        score += max(0.0, 3.0 - min(3.0, fields["break_count"]))
    if fields["break_duration_minutes"] is not None:
        score += 2.0 if fields["break_duration_minutes"] <= 10 else 1.0 if fields["break_duration_minutes"] <= 20 else 0.0
    if fields["reseal_latency_minutes"] is not None:
        score += 2.0 if fields["reseal_latency_minutes"] <= 5 else 1.0 if fields["reseal_latency_minutes"] <= 15 else 0.0
    if fields["close_seal_ratio"] is not None:
        score += min(1.0, max(0.0, fields["close_seal_ratio"]))
    return {"status": "partial" if len(observed) < len(fields) else "observed", "score": round(min(10.0, score), 2), "max_score": 10.0, "fields": fields, "missing": [name for name in fields if name not in observed], "live_effect": "none"}


def next_session_confirmation(
    *, auction_gap_pct: float | None = None, expected_gap_pct: float | None = None,
    first_15m_amount_ratio: float | None = None, theme_relative_strength_pct: float | None = None,
) -> dict[str, Any]:
    """Evaluate next-session evidence as a research confirmation card."""
    values = {"auction_gap_pct": auction_gap_pct, "expected_gap_pct": expected_gap_pct, "first_15m_amount_ratio": first_15m_amount_ratio, "theme_relative_strength_pct": theme_relative_strength_pct}
    missing = [name for name, value in values.items() if value is None]
    if missing:
        return {"status": "not_observed", "confirmed": False, "missing": missing, "live_effect": "none", "notice": "竞价/开盘证据不完整，不能确认龙头强弱。"}
    surprise = float(auction_gap_pct) - float(expected_gap_pct)
    confirmed = surprise >= 0 and float(first_15m_amount_ratio) >= 0.10 and float(theme_relative_strength_pct) >= 0
    return {"status": "confirmed" if confirmed else "rejected", "confirmed": confirmed, "auction_surprise_pct": round(surprise, 4), "first_15m_amount_ratio": first_15m_amount_ratio, "theme_relative_strength_pct": theme_relative_strength_pct, "live_effect": "none", "notice": "仅用于次日研究复核，不构成交易指令。"}


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
    sentiment = market.get("sentiment_metrics") if isinstance(market.get("sentiment_metrics"), dict) else {}
    market_score = min(8.0, float(highest) if highest else 0.0)
    if sentiment.get("promotion_rate") is not None and float(sentiment["promotion_rate"]) >= 0.4:
        market_score += 1.0
    if sentiment.get("high_level_negative_feedback_rate") is not None and float(sentiment["high_level_negative_feedback_rate"]) > 0.3:
        market_score -= 1.0
    components["market_sentiment"] = {"score": round(max(0.0, min(8.0, market_score)), 2), "max_score": 8.0, "status": sentiment.get("quality", "partial"), "metrics": sentiment, "missing": sentiment.get("missing") or ["promotion_rate", "high_level_negative_feedback", "board_repair_rate"]}

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
    components["seal_quality"] = seal_quality(item)

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

    market_component_score = float(components["market_sentiment"]["score"])
    available_score = market_component_score + leader_score + components["theme_strength"]["score"] + chip_score - risk_penalty
    available_max = 8.0 + 20.0 + 15.0 + 20.0
    evidence_max = 8.0 + 20.0 + (15.0 if components["theme_strength"]["status"] == "observed" else 0.0) + (20.0 if components["chip_structure"]["status"] == "observed" else 0.0)
    return {
        "model_version": SCORE_MODEL_VERSION,
        "status": "partial_shadow",
        "live_effect": "none",
        "score": round(max(0.0, available_score), 2),
        "max_available_score": available_max,
        "coverage_ratio": round(evidence_max / 90.0, 3),
        "score_scale": "available_components_only; not comparable to a complete 0-100 live score",
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
    market["sentiment_metrics"] = leader_market_metrics(observable)
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
        item["dragon_leader_watch"]["playbook"] = leader_playbook(item)
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


__all__ = [
    "MODEL_VERSION", "PLAYBOOK_VERSION", "SCORE_MODEL_VERSION",
    "dragon_leader_score", "enrich_dragon_leader_watches",
    "leader_market_metrics", "leader_playbook", "next_session_confirmation",
    "rank_dragon_leader_candidates", "seal_quality",
]
