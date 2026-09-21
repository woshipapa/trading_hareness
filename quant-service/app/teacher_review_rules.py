"""Intraday evaluation of teacher-review plans inside the watchlist scan.

Pure and replayable: the only inputs are the frozen watch row (whose
``metadata.teacher_review`` carries the pre-session plan), the scan's merged
quote (with the raw Longhu row), the causal minute features, the peer context
and the scan clock.  Outputs are ordinary intraday signal candidates tagged
``policy_profile="teacher_review"``; they never size or route an order.

Only ``entry`` and ``invalid`` transitions produce candidates.  A persisting
condition is re-emitted every scan so the existing state machine confirms it
on the second observation and then de-duplicates it; the top-level
conditions intentionally omit price/volume keys so a moving price does not
re-alert the same unchanged setup.  A fresh alert therefore means the setup
lapsed for longer than the confirmation window and triggered again.

Scan-time approximations of the teacher's minute language (all ``I``):
分时放量 = minute volume multiple ≥ 2, or volume ratio ≥ 1.5 with a positive
5-minute return; 不能往下跌 = 5-minute return ≥ -0.3%; 竞价额 = cumulative
turnover observed before 09:31; 封板时成交 = cumulative turnover on the scan
that first sees a sealed bid-only book.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Callable, Mapping
from zoneinfo import ZoneInfo

from .teacher_review_playbooks import DEFAULTS, playbook_kind
from .teacher_review_plan import limit_up_price

_CN_TZ = ZoneInfo("Asia/Shanghai")
MODEL_VERSION = "teacher-review-rules-v1"


def _num(value: Any) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _yi(amount: float) -> str:
    return f"{amount / 1e8:g}亿"


def active_plan(watch: Mapping[str, Any], observed_at: datetime) -> dict[str, Any] | None:
    """Return the plan only on its own session date; anything else is inert."""
    metadata = watch.get("metadata") if isinstance(watch.get("metadata"), Mapping) else {}
    plan = metadata.get("teacher_review")
    if not isinstance(plan, Mapping) or plan.get("status", "active") != "active":
        return None
    if str(plan.get("session_date") or "") != observed_at.astimezone(_CN_TZ).date().isoformat():
        return None
    if playbook_kind(str(plan.get("playbook") or "")) == "record":
        return None
    return dict(plan)


#: Inputs each playbook's gating conditions need.  A missing one makes the
#: plan un-evaluable for that scan and is reported, never treated as a pass.
REQUIRED_INPUTS: dict[str, tuple[str, ...]] = {
    "relay_one_word": ("pre_close", "turnover_pct", "book"),
    "relay_acceleration": ("pre_close", "amount", "vwap"),
    "relay_expect_touch": ("pre_close", "vwap", "surge"),
    "relay_race": ("pre_close", "open", "vwap", "surge"),
    "relay_news_conditional": ("pre_close", "open", "vwap", "surge"),
    "relay_fast_seal": ("pre_close", "open", "amount", "book"),
    "trend_pullback_restart": ("pre_close", "volume_ratio", "vwap"),
    "leader_benchmark_pullback": ("low",),
    "sympathy_follow": ("pre_close", "vwap", "surge"),
    "ma5_reclaim_or_divergence": ("volume_ratio", "vwap", "not_falling", "low"),
    "trend_continuation": ("high",),
    "prior_high_breakout": ("volume_ratio", "vwap", "not_falling"),
    "platform_breakout": ("volume_ratio", "vwap", "not_falling"),
    "ma10_second_wave": ("high", "low", "vwap"),
    "double_bottom_platform": ("volume_ratio", "vwap", "not_falling"),
    "ma60_reclaim": ("amount",),
}


def _pick(*candidates: tuple[str, Any]) -> tuple[float | None, str | None]:
    for source, value in candidates:
        number = _num(value)
        if number is not None and number > 0:
            return number, source
    return None, None


def _tencent_field(row: Mapping[str, Any], index: int) -> Any:
    fields = row.get("raw_fields") if isinstance(row.get("raw_fields"), list) else []
    return fields[index] if len(fields) > index else None


def scan_features(symbol: str, quote: Mapping[str, Any], minute: Mapping[str, Any] | None,
                  observed_at: datetime, name: str = "",
                  previous_quote: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Collect every value the playbooks read, from whichever scan source has it.

    Order: licensed watch quote (Longhu) -> Tencent watch depth quote -> all-A
    snapshot row -> minute features -> arithmetic from the merged quote.
    ``sources`` records where each value came from; ``missing`` lists what no
    source could supply.
    """
    raw = quote.get("raw") if isinstance(quote.get("raw"), Mapping) else {}
    longhu = raw.get("longhu_watch_quote") if isinstance(raw.get("longhu_watch_quote"), Mapping) else {}
    tencent = raw.get("watch_quote") if isinstance(raw.get("watch_quote"), Mapping) else {}
    unfresh = raw.get("longhu_watch_quote_unfresh") if isinstance(raw.get("longhu_watch_quote_unfresh"), Mapping) else {}
    minute = minute if isinstance(minute, Mapping) else {}
    price = float(quote["price"])
    pct_quote = _num(quote.get("pct_change"))
    sources: dict[str, str] = {}

    def take(key: str, *candidates: tuple[str, Any]) -> float | None:
        value, source = _pick(*candidates)
        if source:
            sources[key] = source
        return value

    # pre-close and open are fixed for the session, so an unfresh licensed row is as good as a fresh one.
    pre_close = take("pre_close", ("longhu", longhu.get("pre_close")), ("longhu_unfresh", unfresh.get("pre_close")),
                     ("tencent", tencent.get("pre_close")),
                     ("all_a", raw.get("pre_close")), ("all_a", raw.get("preClose")),
                     ("derived_pct", round(price / (1 + pct_quote / 100), 4) if pct_quote is not None else None))
    opened = take("open", ("longhu", longhu.get("open")), ("longhu_unfresh", unfresh.get("open")),
                  ("tencent", _tencent_field(tencent, 5)), ("all_a", raw.get("open")))
    high = take("high", ("longhu", longhu.get("high")), ("tencent", _tencent_field(tencent, 33)),
                ("all_a", raw.get("high")), ("minute", minute.get("session_high_price")),
                ("longhu_unfresh", unfresh.get("high")))
    low = take("low", ("longhu", longhu.get("low")), ("tencent", _tencent_field(tencent, 34)),
               ("all_a", raw.get("low")), ("minute", minute.get("session_low_price")),
               ("longhu_unfresh", unfresh.get("low")))
    high = max(value for value in (high, price) if value is not None)
    low = min(value for value in (low, price) if value is not None)
    amount = take("amount", ("quote", quote.get("amount")), ("longhu", longhu.get("amount")),
                  ("tencent", tencent.get("cumulative_amount")), ("all_a", raw.get("amount")),
                  ("longhu_unfresh", unfresh.get("amount")))
    volume_lot = take("volume_lot", ("longhu", longhu.get("volume")), ("tencent", tencent.get("cumulative_volume_lot")),
                      ("longhu_unfresh", unfresh.get("volume")),
                      ("quote_shares", (_num(quote.get("volume")) or 0) / 100 or None))
    vwap = take("vwap", ("minute", minute.get("vwap")),
                ("derived_amount_volume", amount / (volume_lot * 100) if amount and volume_lot else None))
    book = longhu.get("order_book") if isinstance(longhu.get("order_book"), Mapping) else None
    if book:
        sources["book"] = "longhu"
    elif tencent.get("book_side"):
        book, sources["book"] = {"book_side": tencent.get("book_side")}, "tencent"
    elif isinstance(unfresh.get("order_book"), Mapping):
        book, sources["book"] = unfresh["order_book"], "longhu_unfresh"
    limit = limit_up_price(pre_close, symbol, name) if pre_close else None
    at_limit = bool(limit) and price >= limit - 0.005
    sealed = at_limit and (book or {}).get("book_side") == "bid_only" if book else None
    volume_ratio = take("volume_ratio", ("quote", quote.get("volume_ratio")), ("longhu_unfresh", unfresh.get("volume_ratio")))
    turnover = take("turnover_pct", ("quote", quote.get("turnover_rate")), ("longhu_unfresh", unfresh.get("turnover_rate")))
    multiple = _num(minute.get("minute_volume_multiple"))
    return_5m = _num(minute.get("return_5m_pct"))
    previous_price = _num((previous_quote or {}).get("price"))
    if multiple is not None:
        surge, sources["surge"] = multiple >= DEFAULTS["minute_volume_multiple_min"] or (
            (volume_ratio or 0) >= DEFAULTS["vol_ratio_min"] and (return_5m or 0) > 0), "minute"
    elif volume_ratio is not None:
        surge, sources["surge"] = volume_ratio >= DEFAULTS["vol_ratio_min"] and (vwap is None or price >= vwap), "volume_ratio"
    else:
        surge = None
    if return_5m is not None:
        not_falling, sources["not_falling"] = return_5m >= DEFAULTS["not_falling_return_5m_min"], "minute"
    elif previous_price:
        not_falling, sources["not_falling"] = price >= previous_price * 0.997, "previous_scan"
    else:
        not_falling = None
    clock = observed_at.astimezone(_CN_TZ).strftime("%H:%M")
    features = {
        "price": price, "pre_close": pre_close,
        "pct": round((price / pre_close - 1) * 100, 2) if pre_close else pct_quote,
        "open": opened, "high": high, "low": low, "amount": amount,
        "turnover_pct": turnover, "volume_ratio": volume_ratio,
        "vwap": round(vwap, 4) if vwap else None, "above_vwap": None if vwap is None else price >= vwap,
        "limit_up_price": limit, "sealed": bool(sealed), "book": sources.get("book"),
        "seal_verified_by_book": book is not None,
        "touched_limit": bool(limit) and high >= limit - 0.005,
        "open_gap_pct": round((opened / pre_close - 1) * 100, 2) if opened and pre_close else None,
        "surge": surge, "not_falling": not_falling,
        "auction_amount": amount if clock < DEFAULTS["auction_proxy_until"] else None,
        "clock": clock, "session_elapsed_min": _elapsed_minutes(observed_at), "sources": sources,
    }
    return features


def missing_inputs(playbook: str, features: Mapping[str, Any]) -> list[str]:
    return [key for key in REQUIRED_INPUTS.get(playbook, ()) if features.get(key) is None]


def _elapsed_minutes(observed_at: datetime) -> int:
    local = observed_at.astimezone(_CN_TZ)
    minutes = local.hour * 60 + local.minute
    return max(0, min(minutes - 570, 120)) + max(0, min(minutes - 780, 120))


def _sig(name: str, value: Any, ok: bool | None, src: str = "", *, gating: bool = True) -> dict[str, Any]:
    return {"name": name, "value": value, "pass": ok, "src": src, "gating": gating}


def _breakout(f: dict[str, Any], level: float | None, label: str) -> list[dict[str, Any]]:
    return [
        _sig(f"价格>{label}", f"{f['price']} vs {level}", level is not None and f["price"] > level),
        _sig(f"量比≥{DEFAULTS['vol_ratio_min']:g}（带量）", f["volume_ratio"], (f["volume_ratio"] or 0) >= DEFAULTS["vol_ratio_min"], "I"),
        _sig("均价上方（不能往下跌）", f"{f['price']} vs {f['vwap']}", bool(f["above_vwap"]), "I"),
        _sig("5分钟不下跌", f["not_falling"], bool(f["not_falling"]), "I"),
    ]


def evaluate(plan: Mapping[str, Any], f: dict[str, Any], peer_context: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Return ``{"action": entry|watch|invalid, "path": ..., "signals": [...]}`` for one snapshot."""
    pb, p = str(plan["playbook"]), plan.get("params") or {}
    x = plan.get("extra") or {}
    sig: list[dict[str, Any]] = []
    invalid = False
    path = None
    if pb == "relay_one_word":
        auction = f["auction_amount"]
        sig += [_sig(f"竞价成交额≥{_yi(p['auction_amount_min'])}", auction,
                     None if auction is None else auction >= p["auction_amount_min"], "T"),
                _sig(f"换手≤{p['turnover_max_pct']:g}%", f["turnover_pct"], (f["turnover_pct"] or 0) <= p["turnover_max_pct"], "T"),
                _sig("封板中", f["sealed"], f["sealed"]),
                _sig(f"最高>前高{p['prior_high']}", f["high"], f["high"] > p["prior_high"], "D", gating=False)]
        invalid = not f["sealed"] and (f["above_vwap"] is False or f["price"] < (f["pre_close"] or 0))
    elif pb == "relay_acceleration":
        amount = f["amount"] or 0
        low, high = p["entry_amount_range"]
        sig += [_sig(f"买点窗口：成交{_yi(low)}~{_yi(high)}且涨幅≥{p['entry_pct_min']:g}%且均价上方",
                     f"{amount / 1e8:.2f}亿/{f['pct']}%",
                     low <= amount <= high * 1.1 and (f["pct"] or 0) >= p["entry_pct_min"] and bool(f["above_vwap"]), "T"),
                _sig(f"加速：封板时成交≤{_yi(p['seal_amount_max'])}", f"{amount / 1e8:.2f}亿/封板={f['sealed']}",
                     f["sealed"] and amount <= p["seal_amount_max"], "T", gating=False)]
        invalid = (amount > p["fail_amount"] and not f["sealed"]) or (f["above_vwap"] is False and not f["sealed"])
        if f["sealed"] and amount <= p["seal_amount_max"]:
            path = "sealed"
    elif pb == "relay_expect_touch":
        sig += [_sig(f"涨幅≥{p['entry_pct_min']:g}% + 分时放量 + 均价上方", f"{f['pct']}%/{f['surge']}/{f['above_vwap']}",
                     (f["pct"] or 0) >= p["entry_pct_min"] and bool(f["surge"]) and bool(f["above_vwap"]), "I"),
                _sig("触及涨停（顶一次）", f["high"], f["touched_limit"], "T", gating=False)]
        invalid = f["price"] < (f["pre_close"] or 0)
    elif pb in ("relay_race", "relay_news_conditional"):
        if pb == "relay_news_conditional":
            sig.append(_sig(f"{p['sector']}板块涨停≥{p['sector_min_limit_ups']}（盘后核对）", None, None, "I", gating=False))
        gap = f["open_gap_pct"]
        sig += [_sig("竞价不低开", gap, gap is None or gap >= 0, "I"),
                _sig("分时放量或已封板", f"{f['surge']}/{f['sealed']}", bool(f["surge"]) or f["sealed"], "I"),
                _sig("均价上方", f["above_vwap"], f["above_vwap"], "I"),
                _sig("封板中", f["sealed"], f["sealed"], gating=False)]
        invalid = (gap is not None and gap < 0 and f["above_vwap"] is False) or f["price"] < (f["pre_close"] or 0)
    elif pb == "relay_fast_seal":
        gap, amount = f["open_gap_pct"], f["amount"] or 0
        sig += [_sig("竞价不低开", gap, gap is None or gap >= 0, "I"),
                _sig(f"封板时成交≤{_yi(p['seal_amount_max'])}", f"{amount / 1e8:.2f}亿/封板={f['sealed']}",
                     f["sealed"] and amount <= p["seal_amount_max"], "I")]
        invalid = (amount >= p["fail_amount"] and not f["sealed"]) or f["price"] < (f["pre_close"] or 0)
    elif pb == "trend_pullback_restart":
        restart = (f["volume_ratio"] or 0) >= DEFAULTS["vol_ratio_min"] and (f["pct"] or 0) >= 3 and bool(f["above_vwap"])
        sig += [_sig("未涨停追高（<9.5%）", f["pct"], (f["pct"] or 0) < 9.5, "T"),
                _sig("前一日已回调缩量", x.get("pulled_back"), bool(x.get("pulled_back")), "I"),
                _sig("再起：量比≥1.5、涨幅≥3%、均价上方", f"{f['volume_ratio']}/{f['pct']}%", restart, "I")]
        invalid = f["price"] < (x.get("ma10") or 0)
    elif pb == "leader_benchmark_pullback":
        level, floor = x.get("pullback_level") or 0, x.get("floor_level") or 0
        sig += [_sig("回踩短均线", f"{f['low']} vs {level}", f["low"] <= level * 1.01, "I"),
                _sig("守趋势均线", f"{f['price']} vs {floor}", f["price"] >= floor, "I")]
        invalid = f["price"] < floor
    elif pb == "sympathy_follow":
        leader = next((item for item in (peer_context or {}).get("peers") or []
                       if isinstance(item, Mapping) and str(item.get("symbol")) == x.get("leader_ts_code")), None)
        strong = None if leader is None else bool(
            (_num(leader.get("above_vwap_pct")) or 0) > 0 and (_num(leader.get("return_from_open_pct")) or 0) >= 0)
        sig += [_sig("龙头走强（均价上方且不低于开盘）", None if leader is None else leader.get("price"), strong, "I", gating=False),
                _sig("分时放量 + 均价上方", f"{f['surge']}/{f['above_vwap']}", bool(f["surge"]) and bool(f["above_vwap"]), "I")]
        invalid = strong is False and f["price"] < (f["pre_close"] or 0)
    elif pb == "ma5_reclaim_or_divergence":
        path_a = _breakout(f, x.get("a_level"), "短均线压制")
        zone = x.get("zone") or [0, 0]
        in_zone = zone[0] <= f["low"] <= zone[1]
        sig += path_a + [_sig("B：进入回踩区", f"{f['low']} in {zone}", in_zone, "T/I", gating=False),
                         _sig("B：30/60分钟两段底背离（盘前）", x.get("divergence"), bool(x.get("divergence_found")), "T", gating=False)]
        invalid = f["price"] < float(p["invalid_below"])
        a_ok = all(item["pass"] for item in path_a)
        b_ok = in_zone and bool(x.get("divergence_found")) and bool(f["above_vwap"])
        path = "A" if a_ok else "B" if b_ok else None
        return {"action": "invalid" if invalid else "entry" if path else "watch", "path": path, "signals": sig}
    elif pb == "trend_continuation":
        hold = x.get("hold_level") or 0
        sig += [_sig("持有：价格≥短均线", f"{f['price']} vs {hold}", f["price"] >= hold, "I"),
                _sig(f"延续：价格>前高{p['prior_high']}", f["high"], f["high"] > p["prior_high"], "D")]
        invalid = f["price"] < (x.get("floor_level") or 0)
    elif pb == "prior_high_breakout":
        sig += _breakout(f, p["prior_high"], "前高")
        invalid = f["price"] < (x.get("floor_level") or 0)
    elif pb == "platform_breakout":
        # 老师：追高是很难的，加自选等回调、走平台；仍在创新高时没有平台可突破。
        sig.append(_sig("已形成平台（高点后整理≥1天）", x.get("days_since_peak"), int(x.get("days_since_peak") or 0) >= 1, "T"))
        sig += _breakout(f, x.get("platform_upper"), "平台上沿")
        floor = min(x.get("platform_lower") or 0, x.get("ma10") or x.get("platform_lower") or 0)
        invalid = f["price"] < floor
    elif pb == "ma10_second_wave":
        ma10 = x.get("ma10") or 0
        touched = bool(x.get("touched_ma10")) or f["low"] <= ma10 * (1 + float(p["touch_tol_pct"]) / 100)
        mid = (f["high"] + f["low"]) / 2
        drift = (mid / x["prior_mid"] - 1) * 100 if x.get("prior_mid") else 0.0
        sig += [_sig("已回到10日线", touched, touched, "T"),
                _sig(f"重心不再下移（≥-{DEFAULTS['center_flat_pct']:g}%）", round(drift, 2), drift >= -DEFAULTS["center_flat_pct"], "I"),
                _sig("均价上方", f["above_vwap"], f["above_vwap"], "I")]
        invalid = f["price"] < ma10 * 0.97
    elif pb == "double_bottom_platform":
        sig += [_sig("颈线上方", f"{f['price']} vs {p['neckline']}", f["price"] >= p["neckline"], "D")]
        sig += _breakout(f, p["platform_high"], "平台高点")
        invalid = f["price"] < p["neckline"]
    elif pb == "ma60_reclaim":
        pace = max(f["session_elapsed_min"], 1) / 240
        projected = (f["amount"] or 0) / pace
        ma60, amt20 = x.get("ma60") or 0, x.get("amt20") or 0
        sig += [_sig("价格>MA60", f"{f['price']} vs {ma60}", f["price"] > ma60, "T"),
                _sig(f"全天成交（外推）≥{p['amount_mult']:g}×20日均额", round(projected / 1e8, 1),
                     projected >= float(p["amount_mult"]) * amt20, "I")]
        invalid = f["price"] < ma60 * 0.97
    gating = [item for item in sig if item["gating"]]
    entry = bool(gating) and all(item["pass"] for item in gating)
    return {"action": "invalid" if invalid else "entry" if entry else "watch", "path": path, "signals": sig}


def teacher_review_signals(
    watch: Mapping[str, Any], quote: Mapping[str, Any] | None, minute_features: Mapping[str, Any] | None,
    peer_context: Mapping[str, Any] | None, observed_at: datetime,
    previous_quote: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Scan hook: entry/invalidation candidates plus an explicit data gap.

    Entry and invalidation carry ``independent_confirmation`` so the first
    scan that satisfies them is confirmed and delivered at once; the shared
    state machine still de-duplicates an unchanged, continuing setup.  A
    missing input is reported as its own candidate after it persists for two
    scans (ordinary confirmation), so the operator learns why a plan is silent.
    """
    plan = active_plan(watch, observed_at)
    if plan is None:
        return []
    symbol = str(watch["symbol"]).upper()
    playbook = str(plan["playbook"])
    base = {
        "symbol": symbol, "hard": False, "policy_profile": "teacher_review", "strategy_version": MODEL_VERSION,
        "risk_flags": ["teacher_review_research", "manual_review_required", "no_automatic_order"],
    }
    review_base = {
        "pack_id": plan.get("pack_id"), "analyst_id": plan.get("analyst_id"), "review_date": plan.get("review_date"),
        "session_date": plan.get("session_date"), "playbook": playbook, "kind": playbook_kind(playbook),
        "stance": plan.get("stance"), "group": plan.get("group"), "name": plan.get("name"),
        "teacher": plan.get("teacher"), "invalidation": plan.get("invalidation"),
        "evidence_times": plan.get("evidence_times"), "model_version": MODEL_VERSION,
    }
    if not quote or _num(quote.get("price")) is None:
        return [{**base, "signal_key": f"{symbol}:data_issue:teacher_review", "signal_type": "data_issue",
                 "severity": "warning", "score": 0,
                 "conditions": {"setup": "teacher_review_data_missing",
                                "teacher_review": {**review_base, "missing": ["price"], "action": "data_missing"}}}]
    features = scan_features(symbol, quote, minute_features, observed_at, str(plan.get("name") or ""), previous_quote)
    missing = missing_inputs(playbook, features)
    result = evaluate(plan, features, peer_context)
    if missing and result["action"] == "entry":
        result = {**result, "action": "watch"}
    signals: list[dict[str, Any]] = []
    feature_view = {key: features[key] for key in (
        "price", "pct", "pre_close", "open", "high", "low", "amount", "turnover_pct", "volume_ratio", "vwap",
        "limit_up_price", "sealed", "seal_verified_by_book", "touched_limit", "open_gap_pct", "surge",
        "not_falling", "auction_amount", "clock", "sources")}
    if result["action"] in {"entry", "invalid"}:
        invalid = result["action"] == "invalid"
        suffix = f":{result['path']}" if result.get("path") else ""
        signals.append({
            **base,
            "signal_key": (f"{symbol}:watch:teacher_review_invalid:{playbook}" if invalid
                           else f"{symbol}:entry:teacher_review:{playbook}{suffix}"),
            "signal_type": "watch" if invalid else "entry",
            "severity": "warning", "score": 50 if invalid else 65,
            "independent_confirmation": True,
            "conditions": {
                "setup": "teacher_review_invalidated" if invalid else "teacher_review_entry",
                "teacher_review": {**review_base, "path": result.get("path"), "action": result["action"],
                                   "signals": result["signals"], "features": feature_view, "missing": missing},
            },
        })
    if missing:
        signals.append({
            **base, "signal_key": f"{symbol}:data_issue:teacher_review", "signal_type": "data_issue",
            "severity": "warning", "score": 0,
            "conditions": {"setup": "teacher_review_data_missing",
                           "teacher_review": {**review_base, "action": "data_missing", "missing": missing,
                                              "features": feature_view}},
        })
    return signals


_INPUT_LABELS = {
    "price": "现价", "pre_close": "昨收", "open": "开盘价", "high": "最高", "low": "最低", "amount": "成交额",
    "turnover_pct": "换手", "volume_ratio": "量比", "vwap": "分时均价", "surge": "分时量能", "not_falling": "5分钟走势",
    "book": "封板盘口",
}


def teacher_review_alert_lines(signal: Mapping[str, Any]) -> list[str]:
    """Feishu body lines for a teacher-review candidate (rendered by intraday_alert_text)."""
    review = (signal.get("conditions") or {}).get("teacher_review") or {}
    features = review.get("features") or {}
    if review.get("action") == "data_missing":
        return [f"老师复盘：{review.get('analyst_id')} {review.get('review_date')}｜打法 {review.get('playbook')}",
                "条件无法判定，缺少数据：" + "、".join(_INPUT_LABELS.get(key, key) for key in review.get("missing") or []),
                "已按数据面现有接口依次尝试 Longhu/腾讯/全A快照/分钟线；请检查对应数据源。"]
    lines = [
        f"老师复盘：{review.get('analyst_id')} {review.get('review_date')}｜{review.get('group') or ''}｜打法 {review.get('playbook')}"
        + (f"（路径 {review['path']}）" if review.get("path") else ""),
        f"老师原话：{str(review.get('teacher') or '')[:160]}",
        f"现价 {features.get('price', '—')}｜涨跌 {features.get('pct', '—')}%｜成交 "
        f"{round((features.get('amount') or 0) / 1e8, 2)}亿｜量比 {features.get('volume_ratio', '—')}｜"
        f"换手 {features.get('turnover_pct', '—')}%｜均价 {features.get('vwap', '—')}｜封板 {features.get('sealed')}",
    ]
    for item in review.get("signals") or []:
        mark = "✔" if item.get("pass") else ("✘" if item.get("pass") is False else "?")
        lines.append(f"{mark} {item.get('name')}：{item.get('value')}" + ("" if item.get("gating", True) else "（参考）"))
    if review.get("action") == "invalid" and review.get("invalidation"):
        lines.append(f"失效条件：{review['invalidation']}")
    if review.get("evidence_times"):
        lines.append(f"视频时间码：{'、'.join(review['evidence_times'][:4])}")
    return lines


__all__ = ["MODEL_VERSION", "active_plan", "evaluate", "scan_features", "teacher_review_alert_lines", "teacher_review_signals"]
