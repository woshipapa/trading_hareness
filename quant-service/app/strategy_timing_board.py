"""Explain when each research strategy can be observed and how its idea is entered.

The timing board is a presentation and replay contract around the existing
strategy ledger.  It deliberately does not add an order gate: a row already
selected by a strategy is shown as a ``research_candidate`` even when one of
the supporting observations is missing.  Missing fields are retained as
evidence for the evening review instead of silently removing the stock.
"""

from __future__ import annotations

from datetime import date, datetime, time
from typing import Any, Mapping, Sequence
from zoneinfo import ZoneInfo

from .platform.strategy_registry import STRATEGY_CONTRACTS

CN_TZ = ZoneInfo("Asia/Shanghai")
VERSION = "strategy-timing-board-v1"

PHASE_ORDER = ("auction", "intraday", "near_close", "post_close")

# These are research instructions, not executable trading rules.  The wording
# is intentionally explicit enough for a person (or the evening AI review) to
# compare the plan with what actually happened in the tape.
TIMING_PROFILES: dict[str, tuple[dict[str, Any], ...]] = {
    "teacher_review_playbooks": (
        {"phase": "auction", "window": "09:15-09:25", "entry_mode": "auction_candidate",
         "buy_point": "09:25 最终竞价快照；一字封死只记录排队，不假设成交",
         "why": "老师复盘给出的次日接力/竞价计划，结合封单、换手和前高观察",
         "evidence": ("auction", "limit_review", "evidence")},
        {"phase": "intraday", "window": "09:30-14:20", "entry_mode": "intraday_confirmation",
         "buy_point": "开板回到分时均价上方、放量重新转强或突破前高时记录",
         "why": "竞价只是计划，盘中用回封、承接和量价确认假设",
         "evidence": ("entry_signals", "themes", "events")},
        {"phase": "near_close", "window": "14:30-14:50", "entry_mode": "close_confirmation",
         "buy_point": "尾盘仍在计划价位上方并保持承接时记录收盘候选",
         "why": "保留未在盘中确认的样本，供盘后按同一口径结算",
         "evidence": ("latest_pct", "entry_signals")},
    ),
    "xiaojie_leader_flow": (
        {"phase": "auction", "window": "09:15-09:25", "entry_mode": "auction_candidate",
         "buy_point": "竞价不弱于预期，开盘后观察龙头承接再记录",
         "why": "龙头资金流与竞价强弱共振，优先看核心而非全板块扩散",
         "evidence": ("auction", "resonance", "themes")},
        {"phase": "intraday", "window": "09:30-14:20", "entry_mode": "intraday_confirmation",
         "buy_point": "放量上攻后回踩 VWAP/均价不破，再次走强时记录",
         "why": "承接、回流和再封比单次冲高更能解释龙头延续",
         "evidence": ("entry_signals", "latest_pct", "themes")},
    ),
    "launch_radar": (
        {"phase": "intraday", "window": "09:30-11:30", "entry_mode": "intraday_confirmation",
         "buy_point": "启动加速后第一次有效回踩、重新站回均价时记录",
         "why": "盘中启动雷达捕捉预封板和板块共振，不把预警直接当成交",
         "evidence": ("entry_signals", "themes", "resonance")},
    ),
    "dragon_leader_research": (
        {"phase": "auction", "window": "09:15-09:25", "entry_mode": "auction_candidate",
         "buy_point": "前一日连板/涨停核心的竞价承接区，记录开盘参考价",
         "why": "连板高度、换手和同题材核心共振是龙头候选的主要理由",
         "evidence": ("auction", "limit_review", "themes")},
        {"phase": "intraday", "window": "09:30-14:20", "entry_mode": "intraday_confirmation",
         "buy_point": "分歧回踩不破关键价后回封或重新放量上攻时记录",
         "why": "把充分换手、承接和大单回流保留为可复盘样本",
         "evidence": ("entry_signals", "latest_pct", "events")},
    ),
    "limit_up_continuation": (
        {"phase": "auction", "window": "09:15-09:25", "entry_mode": "auction_candidate",
         "buy_point": "昨日涨停/连板股按竞价强弱分层记录，开盘即封只标为不可假设成交",
         "why": "提供龙头接力的候选宇宙，强弱分层本身就是研究样本",
         "evidence": ("auction", "limit_review")},
        {"phase": "intraday", "window": "09:30-14:20", "entry_mode": "intraday_confirmation",
         "buy_point": "炸板回封、均价上方承接或核心分歧转一致时记录",
         "why": "连板策略要区分一字不可买与开板换手后的可观察机会",
         "evidence": ("entry_signals", "latest_pct")},
    ),
    "longhu_multifactor_shadow": (
        {"phase": "auction", "window": "09:15-09:25", "entry_mode": "auction_candidate",
         "buy_point": "竞价金额、换手和封单方向与多因子排序一致时记录",
         "why": "龙虎榜、竞价、大单和板块位置共同解释候选，不单看一个指标",
         "evidence": ("auction", "score", "themes")},
        {"phase": "intraday", "window": "09:30-14:50", "entry_mode": "intraday_confirmation",
         "buy_point": "大单净流入延续且价格守住均价/关键位时记录",
         "why": "盘中验证资金是否真的进入，而不是只在竞价留下痕迹",
         "evidence": ("entry_signals", "latest_pct", "events")},
    ),
    "ten_day_leader_rotation_shadow": (
        {"phase": "auction", "window": "09:15-09:25", "entry_mode": "auction_candidate",
         "buy_point": "轮动候选竞价不弱时记录，避免只追已经一字的票",
         "why": "十日强势、VWAP 和板块轮动位置构成接力候选",
         "evidence": ("auction", "score", "themes")},
        {"phase": "intraday", "window": "09:30-14:20", "entry_mode": "intraday_confirmation",
         "buy_point": "轮动方向确认后回踩不破，再次放量时记录",
         "why": "观察资金从旧龙头向新龙头切换的实际承接",
         "evidence": ("entry_signals", "latest_pct", "resonance")},
    ),
    "board_flow_drill": (
        {"phase": "intraday", "window": "09:30-14:20", "entry_mode": "intraday_confirmation",
         "buy_point": "板块净流入与个股领涨同时出现，回踩后再次转强时记录",
         "why": "板块资金流向投射到成员的领涨/跟随关系",
         "evidence": ("themes", "entry_signals", "latest_pct")},
    ),
    "intraday_watchlist_confirmation": (
        {"phase": "intraday", "window": "09:30-14:50", "entry_mode": "intraday_confirmation",
         "buy_point": "价格、分钟量和同板块相对强度同时改善时记录",
         "why": "盘中确认策略本来就是把观察名单变成可复盘候选",
         "evidence": ("entry_signals", "latest_pct", "themes")},
    ),
    "countertrend_rebound_shadow": (
        {"phase": "intraday", "window": "09:45-14:20", "entry_mode": "intraday_confirmation",
         "buy_point": "深水回踩后止跌、承接增强并重新站回均价时记录",
         "why": "极端弱势中的反转承接是样本，不因指数弱就丢弃候选",
         "evidence": ("latest_pct", "entry_signals", "events")},
    ),
    "disclosure_day_watch": (
        {"phase": "intraday", "window": "09:30-14:50", "entry_mode": "intraday_confirmation",
         "buy_point": "披露事件与价格/成交共振时记录为事件观察候选",
         "why": "事件日是容量分配和风险观察，不自动等价为方向买点",
         "evidence": ("events", "latest_pct")},
    ),
    "multi_factor_rank_v1": (
        {"phase": "auction", "window": "09:15-09:25", "entry_mode": "auction_candidate",
         "buy_point": "因子排名靠前且竞价没有明显背离时记录开盘参考价",
         "why": "横截面排名先给出候选，再用竞价作为当天的样本切片",
         "evidence": ("score", "auction")},
        {"phase": "near_close", "window": "14:30-14:50", "entry_mode": "close_confirmation",
         "buy_point": "尾盘仍保持相对强度时记录收盘候选，供次日复盘",
         "why": "保留因子与盘面共振/背离的真实结果",
         "evidence": ("latest_pct", "themes")},
    ),
    "post_close_base_candidates": (
        {"phase": "near_close", "window": "14:30-15:00", "entry_mode": "close_candidate",
         "buy_point": "收盘前确认平台/底部形态仍在，记录次日观察参考价",
         "why": "底部蓄势和新起点属于收盘形成、次日验证的候选",
         "evidence": ("score", "latest_pct", "themes")},
        {"phase": "post_close", "window": "15:10-18:00", "entry_mode": "post_close_replay",
         "buy_point": "盘后把形态、原因和次日假设写入样本，不改变当天候选",
         "why": "收盘后的样本沉淀供 AI 比较不同策略的命中和失效",
         "evidence": ("evidence", "events")},
    ),
    "post_close_limit_lift_pattern": (
        {"phase": "near_close", "window": "14:30-15:00", "entry_mode": "close_candidate",
         "buy_point": "涨停分时形态在收盘前仍保持，记录次日开盘观察价",
         "why": "涨停分时结构是下一交易日接力的形态样本",
         "evidence": ("limit_review", "latest_pct", "score")},
        {"phase": "post_close", "window": "15:10-18:00", "entry_mode": "post_close_replay",
         "buy_point": "盘后结算开盘可买性、回封和次日表现",
         "why": "把一字不可买、开板换手和回封样本分开统计",
         "evidence": ("evidence", "auction")},
    ),
    "watchlist_main_wave_shadow": (
        {"phase": "post_close", "window": "15:10-18:00", "entry_mode": "post_close_replay",
         "buy_point": "仅作负基线样本记录，不改变盘中候选",
         "why": "保留已标记的负基线，帮助 AI 识别错误信号来源",
         "evidence": ("evidence", "score")},
    ),
}


def _phase_at(value: datetime | None) -> str:
    if value is None:
        value = datetime.now(CN_TZ)
    local = value.astimezone(CN_TZ).time()
    if local < time(9, 30):
        return "auction"
    if local < time(14, 30):
        return "intraday"
    if local < time(15, 10):
        return "near_close"
    return "post_close"


def _number(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed == parsed else None


def _profile(strategy_key: str) -> tuple[dict[str, Any], ...]:
    return TIMING_PROFILES.get(strategy_key) or (
        {"phase": "intraday", "window": "09:30-14:50", "entry_mode": "intraday_confirmation",
         "buy_point": "盘中相对强度和承接改善时记录",
         "why": "该策略已有候选，先保留为可复盘研究样本",
         "evidence": ("score", "latest_pct")},
    )


def timing_plans(strategy_key: str) -> list[dict[str, Any]]:
    """Return serializable plans in stable session order."""
    return [dict(item, evidence=list(item["evidence"])) for item in _profile(strategy_key)]


def _reasons(strategy_key: str, pick: Mapping[str, Any], plan: Mapping[str, Any] | None = None) -> list[str]:
    reasons = [str((plan or _profile(strategy_key)[0])["why"])]
    rank = pick.get("rank")
    score = _number(pick.get("score"))
    if rank is not None:
        reasons.append(f"策略排名第 {rank} 位")
    if score is not None:
        reasons.append(f"策略原始分数 {score:g}（保留原策略量纲）")
    evidence = pick.get("evidence") if isinstance(pick.get("evidence"), Mapping) else {}
    source = evidence.get("source")
    if source:
        reasons.append(f"证据来源：{source}")
    if evidence.get("boards") is not None:
        reasons.append(f"连板/高度记录 {float(evidence['boards']):g} 板")
    if evidence.get("bucket"):
        reasons.append(f"竞价分层：{evidence['bucket']}")
    if evidence.get("signal_key"):
        reasons.append(f"触发信号：{evidence['signal_key']}")
    if pick.get("resonance", 0) and int(pick.get("resonance") or 0) > 1:
        reasons.append(f"与 {int(pick['resonance'])} 条策略线共振")
    auction = pick.get("auction") if isinstance(pick.get("auction"), Mapping) else {}
    if _number(auction.get("pct_change")) is not None:
        reasons.append(f"竞价涨跌幅 {float(auction['pct_change']):.2f}%")
    if _number(auction.get("turnover")) is not None:
        reasons.append(f"竞价成交额 {float(auction['turnover']):g}")
    review = pick.get("limit_review") if isinstance(pick.get("limit_review"), Mapping) else {}
    if review:
        reasons.append(f"昨日涨停复盘：{review.get('theme') or review.get('reason') or '有涨停记录'}")
    themes = pick.get("themes") if isinstance(pick.get("themes"), Sequence) else []
    labels = [str(item.get("label")) for item in themes if isinstance(item, Mapping) and item.get("label")]
    if labels:
        reasons.append("题材：" + "、".join(labels[:3]))
    signals = pick.get("entry_signals") if isinstance(pick.get("entry_signals"), Sequence) else []
    if signals:
        reasons.append("盘中已有信号：" + "、".join(str(item.get("model") or "unknown") for item in signals if isinstance(item, Mapping)))
    return reasons


def research_candidate(strategy_key: str, pick: Mapping[str, Any], *, phase: str | None = None) -> dict[str, Any]:
    """Explain one existing pick without filtering it out for missing evidence."""
    plans = _profile(strategy_key)
    active = phase or ""
    plan = next((item for item in plans if item["phase"] == active), plans[0])
    evidence_fields = tuple(plan.get("evidence") or ())
    missing = [field for field in evidence_fields if not pick.get(field)]
    auction = pick.get("auction") if isinstance(pick.get("auction"), Mapping) else {}
    reference = auction.get("price") or auction.get("open") or pick.get("price")
    if active in {"intraday", "near_close", "post_close"}:
        reference = pick.get("price") or reference
    return {
        "strategy_key": strategy_key,
        "symbol": pick.get("symbol"),
        "name": pick.get("name"),
        "status": "research_candidate",
        "research_action": "buy_candidate",
        "phase": plan["phase"],
        "window": plan["window"],
        "entry_mode": plan["entry_mode"],
        "buy_point": plan["buy_point"],
        "entry_reference": reference,
        "selection_reasons": _reasons(strategy_key, pick, plan),
        "missing_evidence": missing,
        "evidence_fields": list(evidence_fields),
        "research_only": True,
        "live_effect": "none",
    }


def build_card_timing(strategy_key: str, picks: Sequence[Mapping[str, Any]], *, observed_at: datetime | None = None) -> dict[str, Any]:
    phase = _phase_at(observed_at)
    return {
        "timing_plans": timing_plans(strategy_key),
        "active_phase": phase,
        "candidates": [research_candidate(strategy_key, pick, phase=phase) for pick in picks],
    }


def build_timing_board(cards: Sequence[Mapping[str, Any]], *, observed_at: datetime | None = None,
                       trade_date: date | None = None) -> dict[str, Any]:
    """Include all registered contracts, even if today's ledger has no row."""
    phase = _phase_at(observed_at)
    cards_by_contract: dict[str, list[Mapping[str, Any]]] = {}
    for card in cards:
        key = str(card.get("contract_key") or card.get("strategy_key") or "")
        if key:
            cards_by_contract.setdefault(key, []).extend(card.get("picks") or [])
    # Ledger-only lines remain visible beside the formal contracts.
    keys = list(STRATEGY_CONTRACTS)
    keys.extend(str(card.get("strategy_key")) for card in cards if not card.get("contract_key"))
    rows = []
    for key in dict.fromkeys(keys):
        contract = STRATEGY_CONTRACTS.get(key)
        picks = cards_by_contract.get(key, [])
        rows.append({
            "strategy_key": key,
            "name": next((str(card.get("name")) for card in cards if card.get("contract_key") == key), key),
            "model_version": contract.model_version if contract else None,
            "maturity": contract.maturity if contract else "ledger_only",
            "status": "candidates_available" if picks else "waiting_for_candidates",
            "active_phase": phase,
            "timing_plans": timing_plans(key),
            "candidates": [research_candidate(key, pick, phase=phase) for pick in picks],
            "research_only": True,
            "live_effect": "none",
        })
    return {
        "version": VERSION,
        "trade_date": trade_date.isoformat() if trade_date else None,
        "active_phase": phase,
        "strategies": rows,
        "research_only": True,
        "live_effect": "none",
        "evening_review": {
            "settlement_source": "quant.strategy_daily_candidate_outcomes",
            "compare_fields": ["entry_reference", "selection_reasons", "phase", "entry_mode", "missing_evidence"],
            "instruction": "盘后保留每个策略当天的候选、买点和理由，再按实际开盘/收盘结果交给 AI 复盘；缺失证据只记录，不删除候选。",
        },
    }


__all__ = [
    "PHASE_ORDER", "TIMING_PROFILES", "VERSION", "build_card_timing", "build_timing_board",
    "research_candidate", "timing_plans",
]
