"""Playbook vocabulary for analyst/teacher post-close review packs.

A review pack is the structured output of one reviewed video or text recap:
per stock, the teacher's stance, a playbook drawn from ``CATALOG`` and the
playbook parameters.  Every numeric parameter carries ``<name>_src``:

* ``T`` - the teacher said the number (time-coded quote in ``evidence``);
* ``D`` - verified against market data or the teacher's on-screen chart;
* ``I`` - an agent default that makes a qualitative phrase testable.

New teacher behaviour means a new playbook here plus its evaluator in
``teacher_review_rules``; a pack can never carry executable logic.  Nothing
in this family authorizes an order (``live_effect="none"``).
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Final

PACK_SCHEMA: Final = "teacher-review-pack/v1"

DEFAULTS: Final[dict[str, Any]] = {
    "vol_ratio_min": 1.5,            # I: “带量” = 量比 ≥ 1.5
    "minute_volume_multiple_min": 2.0,  # I: 分时放量 = 当前分钟量 ≥ 2× 近20分钟中位量
    "not_falling_return_5m_min": -0.3,  # I: “不能往下跌” = 5分钟收益 ≥ -0.3%
    "center_flat_pct": 1.0,          # I: 重心平移 = (高+低)/2 日变动 ≤ 1%
    "auction_proxy_until": "09:31",  # I: 该时刻前的累计成交额视作竞价额（仅在取不到 09:25 竞价快照时）
    "close_confirm_from": "14:50",   # I: “收盘跌破”类失效在该时刻后按当前价确认，之前只提示
}

# kind: relay = next-session relay, trend = multi-session setup, record = settle only (never watched)
CATALOG: Final[dict[str, dict[str, Any]]] = {
    "relay_one_word": {"kind": "relay", "zh": "一字板接力：竞价打满封单额/换手/前高（auction_amount_min = 09:25 封单金额下限）",
                       "required": ("auction_amount_min", "turnover_max_pct", "prior_high")},
    "relay_acceleration": {"kind": "relay", "zh": "放量分歧后次日加速：成交额买点窗口 + 首封额上限",
                           "required": ("seal_amount_max", "entry_amount_range", "entry_pct_min", "fail_amount")},
    "relay_no_chase": {"kind": "record", "zh": "老师明确不追，只记录", "required": ("reenable_amount",)},
    "relay_expect_touch": {"kind": "relay", "zh": "预期次日至少触及涨停", "required": ("entry_pct_min",)},
    "relay_race": {"kind": "relay", "zh": "连板赛马：竞价不低开 + 放量 + 均价上方", "required": ()},
    "relay_fast_seal": {"kind": "relay", "zh": "快速封板：首封额上限 + 失败成交额",
                        "required": ("seal_amount_max", "fail_amount")},
    "relay_news_conditional": {"kind": "relay", "zh": "赌隔夜消息：板块形成才做",
                               "required": ("sector", "sector_min_limit_ups")},
    "trend_pullback_restart": {"kind": "trend", "zh": "突破-杀跌-再起：回调缩量后再起",
                               "required": ("pullback_amount_max",)},
    "leader_benchmark_pullback": {"kind": "trend", "zh": "加速龙头当标杆，回踩短均线",
                                  "required": ("pullback_ma", "trend_floor_ma")},
    "sympathy_follow": {"kind": "relay", "zh": "跟随龙头套利",
                        "required": ("leader", "leader_strong_pct", "leader_weak_pct")},
    "ma5_reclaim_or_divergence": {"kind": "trend", "zh": "A 放量站上短均线 / B 回踩区 + 30/60分钟底背离",
                                  "required": ("prior_high", "support_level", "invalid_below")},
    "trend_continuation": {"kind": "trend", "zh": "趋势延续：守短均线，过前高确认", "required": ("prior_high",)},
    "prior_high_breakout": {"kind": "trend", "zh": "带量突破前高", "required": ("prior_high",)},
    "platform_breakout": {"kind": "trend", "zh": "加自选等回调，平台带量突破", "required": ()},
    "ma10_second_wave": {"kind": "trend", "zh": "回踩10日线后重心走平，做第二轮", "required": ("ma", "touch_tol_pct")},
    "double_bottom_platform": {"kind": "trend", "zh": "双底颈线之上的慢速平台突破",
                               "required": ("neckline", "platform_high")},
    "ma60_reclaim": {"kind": "trend", "zh": "大票带量站上60日线", "required": ("ma", "amount_mult")},
    "rejected": {"kind": "record", "zh": "老师否定，只记录结果", "required": ()},
}

STANCES: Final = frozenset({"positive", "negative", "watch", "unknown"})
FORECAST_KINDS: Final = frozenset({
    "race_sealed_count", "touched_limit", "race_loses", "high_above",
    "sector_limit_ups_at_least", "index_close_at_least",
})
_CODE = re.compile(r"\d{6}")
_ANALYST = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{2,63}")


def playbook_kind(playbook: str) -> str:
    return str(CATALOG.get(playbook, {}).get("kind") or "record")


def ts_code(code: str) -> str:
    return f"{code}.SH" if code.startswith(("6", "9")) else f"{code}.BJ" if code.startswith(("4", "8")) else f"{code}.SZ"


def _iso(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo is not None else None


def validate_pack(pack: Any) -> list[str]:
    """Return human-readable problems; an empty list means the pack is well-formed."""
    if not isinstance(pack, dict):
        return ["pack must be an object"]
    problems: list[str] = []
    if pack.get("schema") != PACK_SCHEMA:
        problems.append(f"schema must be {PACK_SCHEMA}")
    if not re.fullmatch(r"[0-9a-f]{8,64}", str(pack.get("pack_id") or "")):
        problems.append("pack_id must be 8-64 lowercase hex characters")
    analyst = pack.get("analyst") if isinstance(pack.get("analyst"), dict) else {}
    if not _ANALYST.fullmatch(str(analyst.get("analyst_id") or "")):
        problems.append("analyst.analyst_id missing or malformed")
    try:
        datetime.strptime(str(pack.get("review_date")), "%Y-%m-%d")
    except ValueError:
        problems.append("review_date must be YYYY-MM-DD")
    source = pack.get("source") if isinstance(pack.get("source"), dict) else {}
    received, available = _iso(source.get("received_at")), _iso(source.get("strategy_available_at"))
    if received is None or available is None:
        problems.append("source.received_at and source.strategy_available_at must be timezone-aware ISO times")
    elif available < received:
        problems.append("source.strategy_available_at is earlier than source.received_at")
    stocks = pack.get("stocks")
    if not isinstance(stocks, list) or not stocks:
        problems.append("stocks must be a non-empty list")
        stocks = []
    seen: set[str] = set()
    for stock in stocks:
        if not isinstance(stock, dict):
            problems.append("each stock must be an object")
            continue
        code = str(stock.get("code") or "")
        tag = f"{code} {stock.get('name') or ''}".strip()
        if not _CODE.fullmatch(code):
            problems.append(f"{tag}: code must be six digits")
        if code in seen:
            problems.append(f"{tag}: duplicate code")
        seen.add(code)
        playbook = stock.get("playbook")
        if playbook not in CATALOG:
            problems.append(f"{tag}: unknown playbook {playbook}")
            continue
        if stock.get("stance") not in STANCES:
            problems.append(f"{tag}: stance must be one of {sorted(STANCES)}")
        params = stock.get("params") if isinstance(stock.get("params"), dict) else {}
        for name in CATALOG[playbook]["required"]:
            if name not in params:
                problems.append(f"{tag}: {playbook} requires param {name}")
        for name, value in params.items():
            if name.endswith("_src") or isinstance(value, (bool, str)):
                continue
            provenance = str(params.get(f"{name}_src") or "")
            if not provenance or provenance[0] not in "TDI":
                problems.append(f"{tag}: param {name} needs {name}_src starting with T/D/I")
        for name in ("hold_ma", "floor_ma", "pullback_ma", "trend_floor_ma", "support_ma", "invalid_ma", "ma"):
            if name in params and int(params[name]) not in (5, 10, 20, 60):
                problems.append(f"{tag}: {name} must be one of 5/10/20/60")
        evidence = stock.get("evidence")
        if not isinstance(evidence, list) or not evidence or not all(
            isinstance(item, dict) and item.get("time") and item.get("quote") for item in evidence
        ):
            problems.append(f"{tag}: evidence must list time-coded quotes")
        try:
            if int(stock.get("valid_sessions") or 0) < 1:
                raise ValueError
        except (TypeError, ValueError):
            problems.append(f"{tag}: valid_sessions must be ≥ 1")
    superseded = pack.get("supersedes")
    if superseded is not None and (not isinstance(superseded, list) or not all(
            re.fullmatch(r"[0-9a-f]{8,64}", str(item)) for item in superseded)):
        problems.append("supersedes must be a list of pack ids")
    for forecast in pack.get("forecasts") or []:
        check = forecast.get("check") if isinstance(forecast, dict) else None
        if not isinstance(check, dict) or check.get("kind") not in FORECAST_KINDS:
            problems.append(f"forecast {forecast.get('id') if isinstance(forecast, dict) else '?'}: unknown check kind")
    return problems


__all__ = ["CATALOG", "DEFAULTS", "FORECAST_KINDS", "PACK_SCHEMA", "playbook_kind", "ts_code", "validate_pack"]
