"""Research-only quantification of the ``小杰夜报`` leader-flow playbook.

The source material is qualitative.  This module makes each observed rule an
explicit, deterministic feature test over data already retained by the
platform.  It emits a review decision only; it never creates an order or
changes a live threshold.
"""

from __future__ import annotations

from typing import Any, Mapping


MODEL_VERSION = "xiaojie-leader-flow-v3"
INPUT_CONTRACT = "xiaojie-leader-flow-input-v2"

# These are preregistered research defaults, not promoted trading parameters.
# Values which were not stated numerically in the messages remain calibration
# knobs and are returned in ``parameters`` so a walk-forward run can audit them.
DEFAULT_PARAMETERS: dict[str, Any] = {
    "market_gate_min_components": 3,
    # The playbook says "top one or two in the sector", "2-3 days without a new
    # high" and "3-5 day time stop".  Those ranges were previously pinned in
    # code, which put the three most overfit-prone numbers in the module beyond
    # the reach of the walk-forward calibration this file promises.
    # Widened from 2 with FRONT_ROW_MAX_RANK: rank 3 in a broad main sector
    # is core, not chase (002942.SZ sat at rank 3 of 14 boards on 2026-08-28).
    "leader_rank_max": 3,
    "days_without_new_high_min": 3,
    "days_without_rise_min": 5,
    "index_volume_ratio_min": 1.0,
    "sector_strength_percentile_min": 0.80,
    "divergence_drawdown_min_pct": 5.0,
    "divergence_drawdown_max_pct": 7.0,
    "limitup_break_rebound_min_pct": 3.0,
    "limitup_break_rebound_max_pct": 5.0,
    "ma5_break_reduce_minutes": 15,
    "ma5_break_reduce_fraction": 0.50,
    "normal_position_fraction": 0.10,
    "leader_position_fraction": 0.20,
    "high_risk_position_fraction": 0.05,
    "high_risk_total_fraction": 0.10,
    "normal_stop_loss_pct": 5.0,
    "swing_stop_loss_min_pct": 8.0,
    "swing_stop_loss_max_pct": 15.0,
    "short_term_stop_loss_min_pct": 10.0,
    "short_term_stop_loss_max_pct": 20.0,
    "icepoint_ma5_distance_min_pct": 3.0,
    "left_side_trial_fraction": 0.05,
    "oversold_rebound_fraction": 0.05,
    "staged_entry_initial_fraction": 0.50,
    # 潜龙出海_swing is the mode's own catch-all fallback (breakout_or_reverse_wrap
    # with no more specific pattern matched), so it had no daily-bar overheat
    # gate of its own. A same-session event study of 23 "小杰交流" 潜龙出海
    # confirmations (2026-08-26 to 2026-09-16, source-registry tag `xiaojie`)
    # found the single largest effect sizes were board-level, not candlestick:
    # sector_day_return_pct (Cohen's d=1.43) and sector_net_inflow_rate_pct
    # (d=1.04) beat distance_from_ma20_pct (d=0.60) and pre_signal_5d_return_pct
    # (d=0.48), which both beat volume_ratio and close-in-range (d~0). A 4-flag
    # count built from these thresholds separated a 28.6% (0 flags, n=7) from a
    # 75.0% (>=1 flag, n=16) subsequent-decline rate on close-to-close returns
    # through the next 1/3/5/10 sessions. Numbers are directional (n=23, one
    # 3-week window); see qianlong_swing_overheat_flag_count below for the
    # graduated response this backs.
    "qianlong_swing_ma20_distance_max_pct": 15.0,
    "qianlong_swing_pre_run_5d_max_pct": 10.0,
    "qianlong_swing_sector_return_hot_pct": 1.5,
    "qianlong_swing_sector_net_inflow_hot_pct": 3.0,
    "qianlong_swing_divergence_min_pct": 2.0,
    # The 潜龙 five-evidence contract, distilled from the 21 小杰交流 replies
    # (2026-09-17 to 09-22) that name 潜龙/潜龙出海.  They share one order:
    # MA convergence or a box, a marker K on volume (or a second volume
    # reverse-wrap), a pullback that holds MA5 or the box top, sector/main-line
    # confirmation, and fundamentals that are actually delivering.  Look-alike
    # shapes without the last one were explicitly refused (跨境通), as were
    # convergences without a volume marker K (共进股份).  Missing evidence is
    # not passing evidence.  Thresholds below are preregistered calibration
    # knobs.
    #
    # The 潜龙 gates warn rather than block (operator decision, 2026-09-22):
    # a failed or missing evidence item, or three overheat flags, still sends
    # the research reminder, marked with a red warning and its reasons, at
    # the high-risk fraction.  Setting this true restores blocking, so a
    # walk-forward can compare the two policies on the same observations.
    "qianlong_gates_block": False,
    "qianlong_ma_confluence_max_spread_pct": 3.0,
    "qianlong_box_range_max_pct": 20.0,
    "qianlong_marker_k_max_sessions_ago": 5,
    "qianlong_support_band_pct": 1.0,
    # 前高/箱体上沿/套牢盘 pressure only downgrades the research position; the
    # replies treat it as "wait or reduce if it cannot break", not an exit.
    "qianlong_overhead_pressure_band_pct": 3.0,
    "long_term_dca_parts_min": 10,
    "long_term_dca_parts_max": 20,
    "long_term_dca_drawdown_min_pct": 5.0,
    "long_term_dca_drawdown_max_pct": 10.0,
}


#: Exit actions ordered from mildest to strongest.  Any rule that fires may
#: only raise the recommended action, never lower one another rule already set.
EXIT_SEVERITY: dict[str, int] = {
    "hold_or_wait": 0, "reduce_half": 1, "reduce_or_exit": 2, "exit": 3,
}


#: Order in which competing candidates earn a scarce alert slot, following the
#: playbook's own hierarchy: the core leader first, then buy points inside the
#: main line, then the small-position follow-ons.  Without an explicit order a
#: single scan's alerts go out in whatever order the pool happened to be built,
#: and the most numerous mode wins by volume alone - on 2026-08-26's close,
#: 43 of 72 candidates were supplement rotations at a 5% research position,
#: which would have consumed the daily budget ahead of every 20% leader setup.
MODE_ALERT_PRIORITY: dict[str, int] = {
    "leader_pullback": 0,
    "one_word_return_flow": 1,
    "reverse_wrap": 2,
    "divergence_low_suck": 3,
    "right_side_breakout": 4,
    "潜龙出海_swing": 5,
    "etf_trend": 6,
    "oversold_rebound": 7,
    "icepoint_left_trial": 8,
    "supplement_rotation": 9,
}
#: Anything unrecognised sorts after every declared mode rather than ahead of it.
UNRANKED_MODE_PRIORITY = len(MODE_ALERT_PRIORITY)


def alert_priority(candidate: Mapping[str, Any]) -> tuple[int, float]:
    """Sort key for competing candidates: mode conviction, then leader rank.

    Returns a tuple so ties inside a mode fall to the stronger name in its
    sector rather than to whichever row was built first.
    """
    mode = str(candidate.get("mode") or "")
    rank = _number((candidate.get("evidence") or {}).get("candidate_strength_rank"))
    return (MODE_ALERT_PRIORITY.get(mode, UNRANKED_MODE_PRIORITY),
            rank if rank is not None else 99.0)


def research_alert_allowed(candidate: Mapping[str, Any]) -> bool:
    """Whether a research candidate may consume a Xiaojie alert slot.

    Sealed boards remain excluded from ordinary intraday alerts because there
    is no longer an actionable entry/承接 observation.  The 潜龙出海 swing
    mode is explicitly requested as a research reminder, however: a sealed
    candidate may be notified when it is labelled as research-only and never
    promoted to an order path.
    """
    board = candidate.get("evidence") or {}
    board = board.get("board") if isinstance(board, Mapping) else {}
    if not isinstance(board, Mapping) or not board.get("sealed"):
        return True
    return str(candidate.get("mode") or "") == "潜龙出海_swing"


def _number(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed == parsed else None


def _boolean(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None


def _flag(snapshot: Mapping[str, Any], name: str) -> bool:
    """Strict truth test for a snapshot flag.

    Python truthiness is not usable here: a replay whose booleans arrive as
    JSON/CSV strings would read ``"false"`` as true.  That was reachable -
    passing the string ``"false"`` for the one-word-board fields produced a
    high-risk research candidate where a real ``False`` correctly produced
    ``no_trade``.  Anything that is not a genuine bool is treated as absent.
    """
    return _boolean(snapshot.get(name)) is True


def _market_gate(snapshot: Mapping[str, Any], params: Mapping[str, Any]) -> dict[str, Any]:
    index_above_support = _boolean(snapshot.get("index_above_support"))
    volume_ratio = _number(snapshot.get("index_volume_ratio"))
    up_count = _number(snapshot.get("breadth_up_count"))
    down_count = _number(snapshot.get("breadth_down_count"))
    main_sector = _boolean(snapshot.get("main_sector_present"))
    components = {
        "index_above_support": index_above_support is True,
        "index_volume_expansion": volume_ratio is not None and volume_ratio >= float(params["index_volume_ratio_min"]),
        "breadth_improving": up_count is not None and down_count is not None and up_count > down_count,
        "main_sector_present": main_sector is True,
    }
    complete = index_above_support is not None and volume_ratio is not None and up_count is not None and down_count is not None and main_sector is not None
    score = sum(components.values())
    return {
        "score": score,
        "minimum": int(params["market_gate_min_components"]),
        "ok": complete and score >= int(params["market_gate_min_components"]),
        "complete": complete,
        "components": components,
        "breadth_ratio": ((up_count - down_count) / (up_count + down_count)) if up_count is not None and down_count is not None and up_count + down_count > 0 else None,
    }


def _qianlong_swing_overheat(snapshot: Mapping[str, Any], params: Mapping[str, Any]) -> dict[str, Any]:
    """Daily-bar overheat check for the 潜龙出海_swing fallback mode only.

    The other modes each carry their own playbook-stated gate (e.g. a
    confirmed re-seal, a VWAP hold).  潜龙出海_swing is the catch-all for
    "breakout or reverse-wrap, nothing more specific matched", so it had
    nothing of its own.  An absent field raises no flag of its own, but it is
    listed in ``missing``: the caller treats an incomplete reading as unknown
    and funds it at the high-risk fraction rather than as a clean zero-flag
    pass.
    """
    ma20_distance = _number(snapshot.get("distance_from_ma20_pct"))
    pre_run_5d = _number(snapshot.get("pre_signal_5d_return_pct"))
    sector_return = _number(snapshot.get("sector_day_return_pct"))
    sector_inflow = _number(snapshot.get("sector_net_inflow_rate_pct"))
    divergence = _number(snapshot.get("stock_vs_sector_divergence_pct"))
    flags: list[str] = []
    reasons: list[str] = []
    if ma20_distance is not None and ma20_distance > float(params["qianlong_swing_ma20_distance_max_pct"]):
        flags.append("qianlong_swing_extended_above_ma20")
        reasons.append(f"高于20日线{ma20_distance:.0f}%")
    if pre_run_5d is not None and pre_run_5d > float(params["qianlong_swing_pre_run_5d_max_pct"]):
        flags.append("qianlong_swing_pre_run_extended")
        reasons.append(f"5日已涨{pre_run_5d:.0f}%")
    if (sector_return is not None and sector_inflow is not None
            and sector_return > float(params["qianlong_swing_sector_return_hot_pct"])
            and sector_inflow > float(params["qianlong_swing_sector_net_inflow_hot_pct"])):
        flags.append("qianlong_swing_sector_already_hot")
        reasons.append(f"板块已热（涨{sector_return:+.1f}%，净流入率{sector_inflow:+.1f}%）")
    if divergence is not None and divergence < float(params["qianlong_swing_divergence_min_pct"]):
        flags.append("qianlong_swing_no_relative_strength")
        reasons.append(f"相对板块强度仅{divergence:+.1f}%")
    inputs = {"distance_from_ma20_pct": ma20_distance, "pre_signal_5d_return_pct": pre_run_5d,
              "sector_day_return_pct": sector_return, "sector_net_inflow_rate_pct": sector_inflow,
              "stock_vs_sector_divergence_pct": divergence}
    missing = [name for name, value in inputs.items() if value is None]
    return {"flags": flags, "count": len(flags), "reasons": reasons, "missing": missing, "complete": not missing}


#: Chinese names for the overheat inputs, used when one is missing.
QIANLONG_OVERHEAT_INPUT_LABELS: dict[str, str] = {
    "distance_from_ma20_pct": "20日线距离",
    "pre_signal_5d_return_pct": "5日涨幅",
    "sector_day_return_pct": "板块涨幅",
    "sector_net_inflow_rate_pct": "板块净流入率",
    "stock_vs_sector_divergence_pct": "相对板块强度",
}


#: Chinese labels for the five 潜龙 evidence items, in the replies' own order.
QIANLONG_EVIDENCE_LABELS: dict[str, str] = {
    "ma_confluence": "均线收敛/箱体",
    "breakout_volume": "放量标志K",
    "pullback_support": "回踩守支撑",
    "sector_context": "板块主线",
    "fundamental": "基本面兑现",
}


def _qianlong_evidence(snapshot: Mapping[str, Any], params: Mapping[str, Any]) -> dict[str, Any]:
    """The five-evidence contract for 潜龙出海_swing, each item True/False/None.

    None means the inputs to answer it were absent, which is reported as
    missing rather than read as a pass or a fail.
    """
    detail: dict[str, str] = {}

    spread = _number(snapshot.get("ma_spread_min_10d_pct"))
    box_range = _number(snapshot.get("consolidation_box_range_pct"))
    converged = spread is not None and spread <= float(params["qianlong_ma_confluence_max_spread_pct"])
    boxed = box_range is not None and box_range <= float(params["qianlong_box_range_max_pct"])
    if converged or boxed:
        ma_confluence: bool | None = True
        detail["ma_confluence"] = "ma_spread" if converged else "box_range"
    elif spread is not None and box_range is not None:
        ma_confluence = False
    else:
        ma_confluence = None

    marker_ago = _number(snapshot.get("marker_k_sessions_ago"))
    recent_marker = marker_ago is not None and 1 <= marker_ago <= float(params["qianlong_marker_k_max_sessions_ago"])
    today_breakout = _flag(snapshot, "breakout_confirmed")
    today_wrap = _flag(snapshot, "reverse_wrap_volume_confirmed")
    if today_breakout or today_wrap or recent_marker:
        breakout_volume: bool | None = True
        detail["breakout_volume"] = ("breakout_today" if today_breakout
                                     else "volume_reverse_wrap_today" if today_wrap
                                     else f"marker_k_{int(marker_ago)}_sessions_ago")
    elif _boolean(snapshot.get("daily_history_complete")) is True:
        breakout_volume = False
    else:
        breakout_volume = None

    band = float(params["qianlong_support_band_pct"])
    if recent_marker:
        ma5_gap = _number(snapshot.get("signed_distance_from_ma5_pct"))
        box_gap = _number(snapshot.get("distance_from_box_top_pct"))
        holds = [gap >= -band for gap in (ma5_gap, box_gap) if gap is not None]
        pullback_support: bool | None = any(holds) if holds else None
        detail["pullback_support"] = "ma5_or_box_top_after_marker_k"
    elif today_breakout or today_wrap:
        pullback_support = _boolean(snapshot.get("support_or_vwap_holds"))
        detail["pullback_support"] = "vwap_on_marker_day"
    elif breakout_volume is False:
        pullback_support = False
        detail["pullback_support"] = "no_marker_k"
    else:
        pullback_support = None

    in_main = _boolean(snapshot.get("candidate_in_main_sector"))
    percentile = _number(snapshot.get("sector_strength_percentile"))
    strong = percentile is not None and percentile >= float(params["sector_strength_percentile_min"])
    if in_main is True or strong:
        sector_context: bool | None = True
    elif percentile is not None and in_main is False:
        sector_context = False
    else:
        sector_context = None

    pe = _number(snapshot.get("fundamental_pe"))
    fundamental: bool | None = None if pe is None or pe == 0 else pe > 0
    if pe is not None:
        detail["fundamental"] = "profitable_pe_proxy"

    overhead = _number(snapshot.get("overhead_high_distance_pct"))
    pressure = (None if overhead is None
                else 0 < overhead <= float(params["qianlong_overhead_pressure_band_pct"]))

    evidence = {"ma_confluence": ma_confluence, "breakout_volume": breakout_volume,
                "pullback_support": pullback_support, "sector_context": sector_context,
                "fundamental": fundamental}
    missing = [name for name, value in evidence.items() if value is None]
    failed = [name for name, value in evidence.items() if value is False]
    return {"evidence": evidence, "detail": detail, "missing": missing, "failed": failed,
            "complete": not missing, "passed": not missing and not failed,
            "fundamental_pe": pe, "overhead_pressure": pressure, "overhead_distance_pct": overhead}


def _qianlong_warning(evidence: Mapping[str, Any], overheat: Mapping[str, Any]) -> dict[str, Any]:
    """Red when the 潜龙 gates would have blocked, yellow when they downgrade.

    Reasons carry the values behind each call, so the reminder says why - not
    only that - it is being sent under a warning.
    """
    red: list[str] = []
    yellow: list[str] = []
    if evidence["failed"]:
        labels = []
        for name in evidence["failed"]:
            label = QIANLONG_EVIDENCE_LABELS[name]
            if name == "fundamental" and evidence.get("fundamental_pe") is not None:
                label += f"（PE {float(evidence['fundamental_pe']):.1f}，亏损）"
            labels.append(label)
        red.append("潜龙证据不成立：" + "、".join(labels))
    if evidence["missing"]:
        red.append("潜龙证据缺失：" + "、".join(QIANLONG_EVIDENCE_LABELS[name] for name in evidence["missing"]))
    if overheat.get("count", 0) >= 3:
        red.append(f"过热{overheat['count']}项：" + "、".join(overheat.get("reasons") or []))
    elif overheat.get("count", 0) >= 1:
        yellow.append(f"过热{overheat['count']}项：" + "、".join(overheat.get("reasons") or []))
    if overheat.get("missing"):
        yellow.append("过热输入缺失：" + "、".join(
            QIANLONG_OVERHEAT_INPUT_LABELS.get(name, name) for name in overheat["missing"]))
    if evidence.get("overhead_pressure"):
        yellow.append(f"上方前高压力：距60日高点{float(evidence['overhead_distance_pct']):.1f}%")
    level = "red" if red else ("yellow" if yellow else None)
    return {"level": level, "reasons": red + yellow}


def _mode(snapshot: Mapping[str, Any], params: Mapping[str, Any]) -> str | None:
    if _flag(snapshot, "limit_up_return_flow") and _flag(snapshot, "re_seal_confirmed") and _flag(snapshot, "prior_one_word_board"):
        return "one_word_return_flow"
    if _flag(snapshot, "reverse_wrap_confirmed") and _flag(snapshot, "main_sector_present"):
        return "reverse_wrap"
    drawdown = _number(snapshot.get("drawdown_from_high_pct"))
    rebound = _number(snapshot.get("post_limitup_break_rebound_pct"))
    divergence = (
        drawdown is not None and params["divergence_drawdown_min_pct"] <= abs(drawdown) <= params["divergence_drawdown_max_pct"]
    ) or (
        rebound is not None and params["limitup_break_rebound_min_pct"] <= rebound <= params["limitup_break_rebound_max_pct"]
    )
    if divergence and _flag(snapshot, "support_or_vwap_holds"):
        return "divergence_low_suck"
    if _flag(snapshot, "leader_pullback_to_vwap") and _flag(snapshot, "main_sector_present"):
        return "leader_pullback"
    if _flag(snapshot, "index_right_side_confirmed") and _flag(snapshot, "breakout_confirmed"):
        return "right_side_breakout"
    distance_from_ma5 = _number(snapshot.get("distance_from_ma5_pct"))
    if (_flag(snapshot, "icepoint") and _flag(snapshot, "left_side_signal")
            and distance_from_ma5 is not None
            and distance_from_ma5 >= float(params["icepoint_ma5_distance_min_pct"])):
        return "icepoint_left_trial"
    if _flag(snapshot, "oversold_rebound_confirmed") and _flag(snapshot, "support_or_vwap_holds"):
        return "oversold_rebound"
    if _flag(snapshot, "supplement_candidate") and _flag(snapshot, "leader_not_broken"):
        return "supplement_rotation"
    if _flag(snapshot, "is_etf") and _flag(snapshot, "trend_support_holds"):
        return "etf_trend"
    if _flag(snapshot, "breakout_or_reverse_wrap"):
        return "潜龙出海_swing"
    return None


def evaluate_snapshot(snapshot: Mapping[str, Any], parameters: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Evaluate one point-in-time market/candidate snapshot.

    Missing market context fails closed.  The result is deliberately a
    research candidate/watch/no-trade classification with an auditable reason
    list, not a broker instruction.
    """
    supplied = dict(parameters or {})
    unknown = sorted(set(supplied) - set(DEFAULT_PARAMETERS))
    if unknown:
        raise ValueError(f"parameters are not registered: {', '.join(unknown)}")
    params = {**DEFAULT_PARAMETERS, **supplied}
    market = _market_gate(snapshot, params)
    risk_flags: list[str] = []
    reasons: list[str] = []
    if not market["complete"]:
        risk_flags.append("insufficient_market_evidence")
        reasons.append("指数、成交量、宽度和主线板块字段必须完整")
    if not market["ok"]:
        risk_flags.append("market_regime_not_confirmed")
        reasons.append("市场门控未达到预注册最低组件数")
    if _flag(snapshot, "is_back_row"):
        risk_flags.append("back_row_no_chase")
        reasons.append("后排不追高")
    if _flag(snapshot, "futures_stock_both_rising"):
        risk_flags.append("cross_asset_chase_risk")
        reasons.append("期货与股票同步大涨时不追价")

    percentile = _number(snapshot.get("sector_strength_percentile"))
    sector_core = percentile is not None and percentile >= float(params["sector_strength_percentile_min"])
    leader_rank = _number(snapshot.get("candidate_strength_rank"))
    leader_ok = leader_rank is not None and 1 <= leader_rank <= float(params["leader_rank_max"])
    if not sector_core:
        risk_flags.append("sector_core_unconfirmed")
    if not leader_ok:
        risk_flags.append("leader_rank_unconfirmed")

    selected_mode = _mode(snapshot, params)
    high_risk = selected_mode in {"one_word_return_flow", "reverse_wrap"}
    if high_risk:
        risk_flags.append("high_risk_mode")
    is_qianlong = selected_mode == "潜龙出海_swing"
    qianlong_overheat = (
        _qianlong_swing_overheat(snapshot, params) if is_qianlong else {"flags": [], "count": 0}
    )
    risk_flags.extend(qianlong_overheat["flags"])
    qianlong_evidence = _qianlong_evidence(snapshot, params) if is_qianlong else None
    qianlong_overheat_incomplete = is_qianlong and not qianlong_overheat["complete"]
    qianlong_pressure = bool(qianlong_evidence and qianlong_evidence["overhead_pressure"])
    qianlong_warning = (_qianlong_warning(qianlong_evidence, qianlong_overheat)
                        if qianlong_evidence is not None else {"level": None, "reasons": []})
    if qianlong_evidence is not None:
        if qianlong_evidence["missing"]:
            risk_flags.append("qianlong_evidence_incomplete")
        if qianlong_evidence["failed"]:
            risk_flags.append("qianlong_evidence_failed")
    if qianlong_overheat_incomplete:
        # Absent overheat inputs are unknown, not cool: they no longer count as
        # a clean zero-flag reading that funds the normal fraction.
        risk_flags.append("qianlong_overheat_inputs_incomplete")
    if qianlong_pressure:
        risk_flags.append("qianlong_overhead_pressure")
    if qianlong_warning["level"] == "red":
        risk_flags.append("qianlong_red_warning")
    reasons.extend(qianlong_warning["reasons"])
    if selected_mode is None:
        reasons.append("没有可复现的买点形态")
    elif selected_mode == "one_word_return_flow":
        reasons.append("强板块最强活口完成一字板回流并确认回封")
    elif selected_mode == "reverse_wrap":
        reasons.append("板块同步时出现反包/弱转强")
    elif selected_mode == "divergence_low_suck":
        reasons.append("分歧或急跌后回到支撑/VWAP，满足低吸区间")
    elif selected_mode == "leader_pullback":
        reasons.append("核心龙头回踩分时均价且主线仍在")
    elif selected_mode == "right_side_breakout":
        reasons.append("指数右侧确认且个股放量突破")
    elif selected_mode == "icepoint_left_trial":
        reasons.append("冰点偏离 5 日线达到试错距离，仅允许左侧小仓位")
    elif selected_mode == "oversold_rebound":
        reasons.append("超跌反弹确认且支撑/VWAP 未破")
    elif selected_mode == "supplement_rotation":
        reasons.append("主龙头未破坏，板块补涨候选进入轮动")
    elif selected_mode == "etf_trend":
        reasons.append("ETF/低波动资产趋势支撑有效")
    elif qianlong_warning["level"] == "red":
        reasons.append("潜龙出海突破/反包，红色预警，仅作降级波段研究")
    elif qianlong_warning["level"] == "yellow":
        reasons.append("潜龙出海突破/反包，有降级信号，仅作降级波段研究")
    else:
        reasons.append("潜龙出海突破/反包，仅作波段研究")

    is_etf = _flag(snapshot, "is_etf")
    # A supplement is by definition not the sector leader - the playbook allows
    # it precisely as a follow-on, at a small position.  Gating it on "rank 1-2"
    # made the mode unreachable: the live indicator only marks a name as a
    # supplement candidate at rank 3 or worse, so the two intervals never
    # intersected and the mode could select but never produce a candidate.
    # It is exempted from the leader rank and back-row blocks and stays capped
    # at the small high-risk fraction instead.
    follows_a_leader = selected_mode == "supplement_rotation" and _flag(snapshot, "leader_not_broken")
    leader_gate_ok = leader_ok or is_etf or follows_a_leader
    left_side_without_cushion = selected_mode == "icepoint_left_trial" and (_number(snapshot.get("profit_cushion_pct")) or 0) <= 0
    if left_side_without_cushion:
        risk_flags.append("left_side_without_profit_cushion")
    # Graduated on the event-study dose-response above rather than a single
    # cutoff: 0 flags saw a 28.6% subsequent-decline rate (n=7, close enough to
    # a coin flip to still fund at the normal fraction), 1-2 flags saw
    # 66.7-71.4% (n=13, downgraded to the same small fraction as the other
    # high-risk modes rather than funded normally), and 3 flags saw 100%
    # (n=3, small but unanimous - blocked outright rather than sized down).
    # Any 潜龙 warning, red or yellow, funds only the high-risk fraction; a red
    # one blocks only when the preregistered policy switch says so.
    qianlong_overheat_high_risk = is_qianlong and qianlong_warning["level"] is not None
    qianlong_gates_blocked = bool(params["qianlong_gates_block"]) and qianlong_warning["level"] == "red"
    hard_block = (
        not market["ok"] or not market["complete"]
        or (_flag(snapshot, "is_back_row") and not follows_a_leader)
        or _flag(snapshot, "futures_stock_both_rising") or not sector_core or not leader_gate_ok or selected_mode is None
        or left_side_without_cushion or qianlong_gates_blocked
    )
    decision = "no_trade" if hard_block else "research_candidate"
    if decision == "no_trade":
        position_fraction = 0.0
    elif selected_mode == "icepoint_left_trial":
        position_fraction = float(params["left_side_trial_fraction"])
    elif selected_mode in {"oversold_rebound", "supplement_rotation"}:
        position_fraction = float(params["oversold_rebound_fraction"])
    elif qianlong_overheat_high_risk:
        position_fraction = float(params["high_risk_position_fraction"])
    elif high_risk:
        position_fraction = float(params["high_risk_position_fraction"])
    elif selected_mode == "etf_trend":
        position_fraction = float(params["normal_position_fraction"])
    elif selected_mode == "leader_pullback":
        position_fraction = float(params["leader_position_fraction"])
    else:
        position_fraction = float(params["normal_position_fraction"])

    ma5_duration = _number(snapshot.get("ma5_break_duration_minutes"))
    ma5_recovered = _boolean(snapshot.get("ma5_recovered"))
    days_without_new_high = _number(snapshot.get("days_without_new_high"))
    days_without_rise = _number(snapshot.get("days_without_rise"))
    exit_codes: list[str] = []
    triggered: list[str] = []
    if ma5_duration is not None and ma5_duration >= float(params["ma5_break_reduce_minutes"]) and ma5_recovered is False:
        exit_codes.append("ma5_break_unrecovered")
        triggered.append("reduce_half")
    if _flag(snapshot, "box_support_broken") or _flag(snapshot, "entry_low_broken"):
        exit_codes.append("support_break")
        triggered.append("exit")
    if days_without_new_high is not None and days_without_new_high >= float(params["days_without_new_high_min"]):
        exit_codes.append("no_new_high_3d")
        triggered.append("reduce_or_exit")
    if days_without_rise is not None and days_without_rise >= float(params["days_without_rise_min"]):
        exit_codes.append("time_stop_5d")
        triggered.append("exit")
    if _flag(snapshot, "limit_up_break") and _flag(snapshot, "sector_strength_fades"):
        exit_codes.append("limitup_break_sector_fades")
        triggered.append("reduce_or_exit")
    # Severity is a maximum, never last-write-wins.  Sequential assignment let
    # a milder rule evaluated later overwrite a stronger one: a broken support
    # (exit) that had also gone three days without a new high came back as
    # "reduce_or_exit", so accumulating bearish evidence produced a *weaker*
    # recommendation than any one of those conditions alone.
    exit_action = max(triggered, key=EXIT_SEVERITY.get, default="hold_or_wait")

    stop_loss = {
        "mode": "short_term" if high_risk else ("swing" if selected_mode == "潜龙出海_swing" else "normal"),
        "min_pct": float(params["short_term_stop_loss_min_pct"] if high_risk else (params["swing_stop_loss_min_pct"] if selected_mode == "潜龙出海_swing" else params["normal_stop_loss_pct"])),
        "max_pct": float(params["short_term_stop_loss_max_pct"] if high_risk else (params["swing_stop_loss_max_pct"] if selected_mode == "潜龙出海_swing" else params["normal_stop_loss_pct"])),
    }
    staged_entry = decision != "no_trade" and selected_mode not in {"etf_trend"}
    initial_fraction = round(position_fraction * float(params["staged_entry_initial_fraction"]), 4) if staged_entry else round(position_fraction, 4)
    confirmation_fraction = round(position_fraction - initial_fraction, 4) if staged_entry else 0.0
    return {
        "strategy_key": "xiaojie_leader_flow",
        "model_version": MODEL_VERSION,
        "input_contract": INPUT_CONTRACT,
        "decision": decision,
        "mode": selected_mode,
        "market_gate": market,
        "qianlong_swing_overheat": qianlong_overheat,
        "qianlong_evidence": qianlong_evidence,
        "qianlong_warning": qianlong_warning,
        "position": {
            "target_fraction": round(position_fraction, 4),
            "staged_entry": staged_entry,
            "initial_fraction": initial_fraction,
            "confirmation_fraction": confirmation_fraction,
            "high_risk_total_cap_fraction": float(params["high_risk_total_fraction"]),
        },
        "portfolio_policy": {
            "hierarchy": ["risk_management", "market_regime", "style", "sector", "stock", "entry_exit"],
            "allocation": "single_symbol_10_to_20_percent; at_least_two_sectors; reserve_cash",
            "long_term_dca": {
                "parts_min": int(params["long_term_dca_parts_min"]),
                "parts_max": int(params["long_term_dca_parts_max"]),
                "buy_on_drawdown_pct": [float(params["long_term_dca_drawdown_min_pct"]), float(params["long_term_dca_drawdown_max_pct"])],
            },
        },
        "stop_loss": stop_loss,
        "exit": {"action": exit_action, "codes": exit_codes},
        "reasons": reasons,
        "risk_flags": list(dict.fromkeys(risk_flags)),
        "parameters": params,
        "live_effect": "none",
        "boundary": "research_only; point_in_time_snapshot; no_automatic_order",
    }


__all__ = [
    "DEFAULT_PARAMETERS", "EXIT_SEVERITY", "INPUT_CONTRACT", "MODEL_VERSION",
    "MODE_ALERT_PRIORITY", "QIANLONG_EVIDENCE_LABELS", "QIANLONG_OVERHEAT_INPUT_LABELS", "UNRANKED_MODE_PRIORITY", "alert_priority", "research_alert_allowed",
    "evaluate_snapshot",
]
