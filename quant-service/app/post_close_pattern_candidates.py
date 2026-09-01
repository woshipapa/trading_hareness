"""Pure selection of post-close limit-up pattern research samples."""

from __future__ import annotations

from datetime import date
import math
from typing import Any, Callable

from .limit_continuation_research import continuation_watch
from .dragon_leader_research import enrich_dragon_leader_watches


def select_candidates(
    as_of_date: date, max_symbols: int, per_cohort: int,
    limit_rows: list[dict[str, Any]], step_rows: list[dict[str, Any]],
    prior_limit_rows: list[dict[str, Any]], control_rows: list[dict[str, Any]],
    daily_rows: list[dict[str, Any]],
    boards: dict[str, dict[str, Any]], lhb_by_symbol: dict[str, dict[str, Any]],
    focus_symbols: list[str] | None, *,
    limit_daily_features: Callable[[list[dict[str, Any]]], dict[str, Any]],
    board_count: Callable[[Any], int],
) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in daily_rows:
        item = dict(row)
        grouped.setdefault(str(item["symbol"]), []).append(item)
    steps = {str(row.get("ts_code") or "").upper(): int(row.get("nums") or 0) for row in step_rows}
    prior_stamp = None
    if prior_limit_rows:
        prior_stamp = str(prior_limit_rows[0].get("trade_date") or "") or None
    prior_ranked = sorted(
        (dict(row) for row in prior_limit_rows),
        key=lambda raw: (-board_count(raw.get("tag")), -float(raw.get("limit_amount") or 0),
                         -float(raw.get("turnover_rate") or 0), str(raw.get("ts_code") or "")),
    )
    prior_context = {
        str(raw.get("ts_code") or "").upper(): {**raw, "preopen_limit_pool_rank": rank, "trade_date": prior_stamp}
        for rank, raw in enumerate(prior_ranked, start=1)
    }
    items: list[dict[str, Any]] = []
    for stored in limit_rows:
        raw = dict(stored.get("row_data") or stored)
        symbol = str(raw.get("ts_code") or "").upper()
        if symbol not in grouped:
            continue
        daily = limit_daily_features(grouped[symbol])
        board = boards.get(symbol) or {"exact_member_mapping": False}
        lhb_context = lhb_by_symbol.get(symbol)
        streak = max(steps.get(symbol, 0), board_count(raw.get("tag")))
        continuation = continuation_watch(
            {**raw, "streak_count": streak, "sources": ["tushare_limit_list_ths"]},
            number=lambda value: float(value) if value is not None else None,
            board_count=board_count,
        )
        cohorts: list[str] = []
        if daily.get("ground_to_sky_daily_shape"):
            cohorts.append("ground_to_sky")
        if float(board.get("net_amount") or 0) > 0:
            cohorts.append("board_leader")
        if streak >= 2:
            cohorts.append("consecutive_limit")
        if str(raw.get("tag") or "") == "首板":
            cohorts.append("first_board")
        if continuation.get("eligible"):
            cohorts.append("limit_continuation_watch")
        if not cohorts:
            continue
        limit_amount = float(raw.get("limit_amount") or 0)
        turnover = float(raw.get("turnover_rate") or 0)
        selection_score = streak * 20 + float(board.get("flow_percentile") or 0) * 18 + min(18, math.log10(max(1, limit_amount)) * 2)
        if daily.get("ground_to_sky_daily_shape"):
            selection_score += 35
        if lhb_context and float(lhb_context.get("institution_net_buy") or 0) > 0:
            selection_score += 10
        elif lhb_context and float(lhb_context.get("institution_net_buy") or 0) < 0:
            selection_score -= 4
        if continuation.get("eligible"):
            selection_score += 12
        risk_flags: list[str] = []
        if str(raw.get("status") or "") == "一字板":
            risk_flags.append("one_word_board_not_intraday_entry_sample")
        if turnover >= 35:
            risk_flags.append("extreme_turnover")
        if daily.get("ground_to_sky_daily_shape"):
            risk_flags.append("deep_reversal_extreme_volatility")
        if not board.get("exact_member_mapping"):
            risk_flags.append("no_exact_ths_concept_mapping")
        elif float(board.get("net_amount") or 0) <= 0:
            risk_flags.append("nonpositive_exact_board_flow")
        if lhb_context and float(lhb_context.get("institution_net_buy") or 0) < 0:
            risk_flags.append("lhb_institution_net_sell")
        selection_reasons: list[str] = []
        if streak >= 2:
            selection_reasons.append(f"{streak}板梯队")
        if continuation.get("eligible"):
            selection_reasons.append("连板+封单强度下一交易日复核")
        if daily.get("ground_to_sky_daily_shape"):
            selection_reasons.append("深水反转日线")
        if float(daily.get("volume_multiple_5d") or 0) >= 1.5:
            selection_reasons.append(f"5日量能{float(daily['volume_multiple_5d']):.2f}倍")
        if float(board.get("net_amount") or 0) > 0:
            selection_reasons.append(f"{board.get('label') or '精确板块'}资金为正")
        if lhb_context:
            direction = "净买" if float(lhb_context.get("institution_net_buy") or 0) > 0 else "净卖"
            selection_reasons.append(f"龙虎榜机构{direction}{abs(float(lhb_context.get('institution_net_buy') or 0)) / 10_000:.0f}万")
        items.append({"symbol": symbol, "name": raw.get("name"), "cohorts": cohorts, "board_context": board,
                      "limit_context": {**raw, "provider_key": stored.get("provider_key"), "streak_count": streak,
                                        "sample_role": "positive_limit_pool",
                                        "continuation_watch": continuation,
                                        "preopen_context": prior_context.get(symbol),
                                        "preopen_limit_pool_rank": (prior_context.get(symbol) or {}).get("preopen_limit_pool_rank"),
                                        "lhb_context": lhb_context, "selection_reasons": selection_reasons},
                      "daily_features": daily, "selection_score": round(selection_score, 3), "risk_flags": risk_flags})

    leader_market_context = enrich_dragon_leader_watches(items)
    for item in items:
        leader = item.get("dragon_leader_watch") or {}
        item["limit_context"]["dragon_leader_watch"] = leader
        if leader.get("eligible"):
            item["cohorts"].append("dragon_leader_watch")

    focus_set = set(focus_symbols or [])
    for item in items:
        if item["symbol"] in focus_set:
            item["cohorts"].append("focus")
        if int(item["limit_context"].get("preopen_limit_pool_rank") or 10_000) <= 10:
            item["cohorts"].append("preopen_market_leader")
    market_leaders = sorted(
        (item for item in items if int(item["limit_context"].get("streak_count") or 0) >= 2),
        key=lambda item: (-int(item["limit_context"].get("streak_count") or 0), -float(item["selection_score"]), item["symbol"]),
    )
    for market_rank, item in enumerate(market_leaders, start=1):
        item["limit_context"]["limit_pool_market_rank"] = market_rank
        if market_rank <= 10:
            item["cohorts"].append("market_leader")
    cohort_order = ("focus", "dragon_leader_watch", "limit_continuation_watch", "ground_to_sky", "preopen_market_leader", "market_leader", "board_leader", "consecutive_limit", "first_board")
    selected: list[dict[str, Any]] = []
    seen: set[str] = set()
    for cohort in cohort_order:
        ranked = sorted((item for item in items if cohort in item["cohorts"]), key=lambda item: (-float(item["selection_score"]), item["symbol"]))
        for item in ranked[:per_cohort]:
            if item["symbol"] in seen:
                continue
            selected.append({**item, "primary_cohort": cohort})
            seen.add(item["symbol"])
            if len(selected) >= max_symbols:
                break
        if len(selected) >= max_symbols:
            break
    if len(selected) < max_symbols:
        for item in sorted(items, key=lambda item: (-float(item["selection_score"]), item["symbol"])):
            if item["symbol"] not in seen:
                selected.append({**item, "primary_cohort": item["cohorts"][0]})
                seen.add(item["symbol"])
            if len(selected) >= max_symbols:
                break
    def number(value: Any) -> float | None:
        try:
            return float(value) if value not in (None, "") else None
        except (TypeError, ValueError):
            return None

    def limit_bucket(daily: dict[str, Any]) -> int | None:
        value = number(daily.get("limit_pct"))
        return int(round(value)) if value is not None and value > 0 else None

    controls_by_bucket: dict[int | None, list[dict[str, Any]]] = {}
    for raw in control_rows:
        control = dict(raw)
        symbol = str(control.get("symbol") or "").upper()
        daily = limit_daily_features(grouped.get(symbol, []))
        if not symbol or daily.get("status") != "completed":
            continue
        controls_by_bucket.setdefault(limit_bucket(daily), []).append({
            "symbol": symbol, "name": control.get("name"), "daily_features": daily,
            "limit_gap_pct": number(control.get("limit_gap_pct")),
            "selected_provider": control.get("selected_provider"),
        })
    for controls in controls_by_bucket.values():
        controls.sort(key=lambda item: (float(item.get("limit_gap_pct") or 10_000), item["symbol"]))

    selected_controls: list[dict[str, Any]] = []
    used_controls: set[str] = set()
    control_budget = min(len(control_rows), max(0, int(max_symbols)) * 2)
    for positive in selected:
        if len(selected_controls) >= control_budget:
            break
        bucket = limit_bucket(positive["daily_features"])
        matches = [*controls_by_bucket.get(bucket, []), *controls_by_bucket.get(None, [])]
        for control in matches:
            if control["symbol"] in used_controls:
                continue
            used_controls.add(control["symbol"])
            gap = control.get("limit_gap_pct")
            selected_controls.append({
                "symbol": control["symbol"], "name": control.get("name"),
                "primary_cohort": "matched_near_limit_control",
                "cohorts": ["matched_near_limit_control"],
                "board_context": {"status": "not_required_for_negative_control", "exact_member_mapping": False},
                "limit_context": {
                    "sample_role": "matched_near_limit_control", "matched_to_symbol": positive["symbol"],
                    "match_basis": {"limit_ratio_bucket_pct": bucket,
                                    "limit_gap_pct": round(gap, 4) if gap is not None else None,
                                    "selection": "same-day non-limit close nearest to its own limit price"},
                    "source": "canonical_bars_daily", "provider_key": control.get("selected_provider"),
                },
                "daily_features": control["daily_features"],
                "selection_score": round(-(gap or 0), 4),
                "risk_flags": ["matched_negative_control", "not_limit_up", "not_a_candidate"],
            })
            matched = sum(item["limit_context"].get("matched_to_symbol") == positive["symbol"] for item in selected_controls)
            if len(selected_controls) >= control_budget or matched >= 2:
                break

    return {"status": "completed" if selected else "blocked", "as_of_date": str(as_of_date),
            "limit_pool_rows": len(limit_rows), "limit_step_rows": len(step_rows),
            "candidates": [*selected, *selected_controls],
            "sample_role_counts": {"positive_limit_pool": len(selected), "matched_near_limit_control": len(selected_controls)},
            "control_coverage": {
                "status": "completed" if len(selected_controls) >= min(control_budget, len(selected) * 2) else "partial",
                "available_near_limit_controls": sum(len(value) for value in controls_by_bucket.values()),
                "selected_controls": len(selected_controls), "target_controls": min(control_budget, len(selected) * 2),
                "notice": "negative controls are research-only and never enter candidate or recommendation paths",
            },
            "cohort_counts": {**{cohort: sum(cohort in item["cohorts"] for item in items) for cohort in cohort_order},
                              "matched_near_limit_control": len(selected_controls)},
            "dragon_leader_market_context": leader_market_context}


__all__ = ["select_candidates"]
