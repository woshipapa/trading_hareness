"""Freeze a teacher-review stock plan from bars known before the session.

Pure functions only.  ``plan_stock`` receives forward-adjusted daily bars that
end at the last completed session before the target session and returns the
compact runtime plan stored in ``intraday_watchlists.metadata.teacher_review``.
The intraday rule never reads bars itself, so the plan is the point-in-time
boundary: a level computed here cannot see the session it is evaluated in.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Iterable, Mapping

from .teacher_review_playbooks import DEFAULTS


def parse_longhu_kline(pages: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Normalize ``GetKLineDay_W14`` payloads (y = [open, close, high, low]) oldest first."""
    bars: dict[str, dict[str, Any]] = {}
    for payload in pages:
        if not isinstance(payload, Mapping):
            continue
        dates, ohlc = payload.get("x") or [], payload.get("y") or []
        volumes, amounts = payload.get("vol") or [], payload.get("bal") or []
        turnovers, limit_flags = payload.get("turnover") or [], payload.get("stateZT") or []
        for index, stamp in enumerate(dates):
            try:
                opened, closed, high, low = (float(value) for value in ohlc[index][:4])
            except (TypeError, ValueError, IndexError):
                continue
            bars[str(stamp)] = {
                "date": str(stamp), "open": opened, "close": closed, "high": high, "low": low,
                "volume_lot": float(volumes[index]) if index < len(volumes) else 0.0,
                "amount": float(amounts[index]) if index < len(amounts) else 0.0,
                "turnover": float(turnovers[index]) if index < len(turnovers) else None,
                "limit_up": bool(limit_flags[index]) if index < len(limit_flags) else False,
            }
    return [bars[key] for key in sorted(bars)]


def sma(values: list[float], window: int) -> float | None:
    return round(sum(values[-window:]) / window, 4) if len(values) >= window else None


def _ema(values: list[float], window: int) -> list[float]:
    result: list[float] = []
    alpha = 2 / (window + 1)
    for value in values:
        result.append(value if not result else result[-1] + alpha * (value - result[-1]))
    return result


def bullish_divergence(bars: list[dict[str, Any]], *, lookback: int = 60, swing: int = 3) -> dict[str, Any]:
    """Two-leg bottom divergence: a lower (≤) swing low with a higher MACD DIF and a ≥3% bounce between."""
    if len(bars) < 35:
        return {"found": False, "reason": "bars<35"}
    closes = [float(bar["close"]) for bar in bars]
    dif = [fast - slow for fast, slow in zip(_ema(closes, 12), _ema(closes, 26))]
    lows = [float(bar["low"]) for bar in bars]
    pivots: list[int] = []
    for index in range(max(swing, len(bars) - lookback), len(bars)):
        if lows[index] != min(lows[max(0, index - swing): index + swing + 1]):
            continue
        if pivots and index - pivots[-1] < 2 * swing:
            if lows[index] <= lows[pivots[-1]]:
                pivots[-1] = index
            continue
        pivots.append(index)
    if len(pivots) < 2:
        return {"found": False, "reason": "swing lows<2"}
    first, second = pivots[-2], pivots[-1]
    bounce = max(float(bar["high"]) for bar in bars[first:second + 1]) / lows[first] - 1
    return {
        "found": lows[second] <= lows[first] * 1.005 and dif[second] > dif[first] and bounce >= 0.03,
        "low1": [bars[first]["date"], lows[first], round(dif[first], 4)],
        "low2": [bars[second]["date"], lows[second], round(dif[second], 4)],
    }


DIVERGENCE_MIN_BARS = 35


def divergence_status(bars: list[dict[str, Any]], source: str | None) -> dict[str, Any]:
    """``bullish_divergence`` plus whether it could be judged at all.

    ``status`` is ``ok`` when there were enough bars; ``insufficient_bars``
    means "not judgeable", which must never read as "no divergence".
    """
    result = bullish_divergence(bars) if bars else {"found": False, "reason": "no bars"}
    return {
        "found": bool(result.get("found")),
        "status": "ok" if len(bars) >= DIVERGENCE_MIN_BARS else "insufficient_bars",
        "bars": len(bars), "source": source, "through": bars[-1]["date"] if bars else None,
        **{key: result[key] for key in ("low1", "low2", "reason") if key in result},
    }


def divergence_summary(divergence: Mapping[str, Any] | None) -> tuple[bool, bool]:
    """(found, judgeable) over the periods; tolerates the v1 ``{period: bool}`` form."""
    found = judgeable = False
    for value in (divergence or {}).values():
        if isinstance(value, Mapping):
            found = found or bool(value.get("found"))
            judgeable = judgeable or value.get("status") == "ok"
        elif isinstance(value, bool):
            found = found or value
    return found, judgeable


def limit_up_price(pre_close: float, code: str, name: str = "") -> float:
    """Exchange-rounded limit price for main/ChiNext/STAR/BSE boards and ST names."""
    digits = str(code)[:6]
    if "ST" in str(name).upper():
        pct = Decimal("0.05")
    elif digits.startswith(("300", "301", "688", "689")):
        pct = Decimal("0.2")
    elif digits.startswith(("4", "8", "92")):
        pct = Decimal("0.3")
    else:
        pct = Decimal("0.1")
    return float((Decimal(str(pre_close)) * (1 + pct)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


MA_PERIODS = (5, 10, 20, 60)


def daily_levels(bars: list[dict[str, Any]]) -> dict[str, Any]:
    closes = [float(bar["close"]) for bar in bars]
    amounts = [float(bar["amount"]) for bar in bars]
    last = bars[-1]
    window = bars[-20:]
    peak_index = max(range(len(window)), key=lambda index: window[index]["high"])
    platform = window[peak_index:]
    trs = [max(bar["high"], prev["close"]) - min(bar["low"], prev["close"]) for prev, bar in zip(bars[:-1], bars[1:])]
    return {
        "date": last["date"], "close": last["close"], "high": last["high"], "low": last["low"],
        "amount": last["amount"],
        # Sum of the previous n-1 closes: today's MA_n = (prefix + price) / n, exact intraday.
        "ma_prefix": {str(n): round(sum(closes[-(n - 1):]), 6) for n in MA_PERIODS if len(closes) >= n - 1},
        "atr14": round(sum(trs[-14:]) / 14, 4) if len(trs) >= 14 else None,
        "ma5": sma(closes, 5), "ma10": sma(closes, 10), "ma20": sma(closes, 20),
        "ma60": sma(closes, 60), "ma250": sma(closes, 250), "amt20": sma(amounts, 20),
        "peak20": window[peak_index]["high"], "peak20_date": window[peak_index]["date"],
        "days_since_peak": len(platform) - 1,
        "platform_upper": max(bar["high"] for bar in platform),
        "platform_lower": min(bar["low"] for bar in platform),
        "mid": round((last["high"] + last["low"]) / 2, 4),
        "mid_prev": round((bars[-2]["high"] + bars[-2]["low"]) / 2, 4) if len(bars) > 1 else None,
    }


def _yi(amount: float) -> str:
    return f"{amount / 1e8:g}亿"


def plan_stock(
    stock: Mapping[str, Any], bars: list[dict[str, Any]], *,
    divergence: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return the frozen runtime plan: ``levels`` (subset), ``extra``, ``setup`` and ``checklist``."""
    lv = daily_levels(bars)
    p = stock.get("params") or {}
    playbook = str(stock["playbook"])
    extra: dict[str, Any] = {"limit_up_price": limit_up_price(lv["close"], str(stock["code"]), str(stock.get("name") or "")),
                             "ma_prefix": lv["ma_prefix"], "atr14": lv["atr14"]}
    setup, checklist = "", []
    if playbook == "relay_one_word":
        setup = f"一字板，收 {lv['close']}，距前高 {p['prior_high']} 差 {round((p['prior_high'] / lv['close'] - 1) * 100, 2)}%"
        checklist = [f"09:25 竞价打满（涨停价）且封单 ≥ {_yi(p['auction_amount_min'])}", f"最高 > 前高 {p['prior_high']}（参考）",
                     f"换手 ≤ {p['turnover_max_pct']:g}%", "开板后跌破分时均价或昨收 → 失效"]
    elif playbook == "relay_acceleration":
        low, high = p["entry_amount_range"]
        setup = f"放量分歧（{lv['amount'] / 1e8:.2f}亿），次日看加速"
        checklist = [f"买点：成交 {_yi(low)}~{_yi(high)} 且涨幅 ≥ {p['entry_pct_min']:g}% 且在均价上方",
                     f"加速成功：封板时成交 ≤ {_yi(p['seal_amount_max'])}", f"成交 > {_yi(p['fail_amount'])} 仍未封 → 失效"]
    elif playbook == "relay_expect_touch":
        setup = f"预期触及涨停 {extra['limit_up_price']}"
        checklist = ["至少触及涨停一次", f"跟随：涨幅 ≥ {p['entry_pct_min']:g}% + 分时放量 + 均价上方", "跌破昨收 → 失效"]
    elif playbook in ("relay_race", "relay_news_conditional"):
        setup = f"赛马，涨停价 {extra['limit_up_price']}"
        checklist = ["竞价不低开", "分时放量或封板", "价格在分时均价上方"]
        if playbook == "relay_news_conditional":
            checklist.insert(0, f"前提：{p['sector']} 板块涨停 ≥ {p['sector_min_limit_ups']} 家（盘后核对）")
    elif playbook == "relay_fast_seal":
        setup = f"快速封板，涨停价 {extra['limit_up_price']}"
        checklist = [f"封板时成交 ≤ {_yi(p['seal_amount_max'])}", "竞价不低开", f"成交 ≥ {_yi(p['fail_amount'])} 仍未封 → 失效"]
    elif playbook == "trend_pullback_restart":
        pulled = lv["close"] <= (lv["ma5"] or 0) * 1.02 or lv["amount"] <= p["pullback_amount_max"] * 0.8
        extra.update({"pulled_back": pulled, "ma10": lv["ma10"]})
        setup = "已回调缩量，等再起" if pulled else "仍在连板/突破段，不追"
        checklist = ["涨幅 ≥ 9.5% 不追", "前一日已回调缩量", "再起：量比 ≥ 1.5、涨幅 ≥ 3%、均价上方", f"跌破 MA10={lv['ma10']} → 失效"]
    elif playbook == "leader_benchmark_pullback":
        ma, floor = f"ma{p['pullback_ma']}", f"ma{p['trend_floor_ma']}"
        extra.update({"pullback_level": lv[ma], "floor_level": lv[floor],
                      "pullback_ma": int(p["pullback_ma"]), "trend_floor_ma": int(p["trend_floor_ma"])})
        setup = f"加速龙头（收 {lv['close']}，{ma.upper()} {lv[ma]}，{floor.upper()} {lv[floor]}），不追"
        checklist = [f"回踩 {ma.upper()}（≤{round((lv[ma] or 0) * 1.01, 2)}）且守 {floor.upper()} → 观察", f"跌破 {floor.upper()} → 趋势结束"]
    elif playbook == "sympathy_follow":
        leader_code = str(p["leader"])[:6]
        extra["leader_ts_code"] = f"{leader_code}.SH" if leader_code.startswith(("6", "9")) else f"{leader_code}.SZ"
        leader = p.get("leader_name") or p["leader"]
        setup = f"跟随 {leader}"
        checklist = [f"{leader} 强（涨幅 ≥ {p['leader_strong_pct']:g}% 或封板）", "自身分时放量 + 均价上方", f"{leader} 跌 ≥ {abs(p['leader_weak_pct']):g}% → 失效"]
    elif playbook == "ma5_reclaim_or_divergence":
        a_level = max(lv["ma5"] or 0, lv["ma10"] or 0)
        support_ma = lv.get(f"ma{int(p['support_ma'])}") if p.get("support_ma") else None
        anchors = [float(p["support_level"])] + ([support_ma] if support_ma else [])
        zone = [round(min(anchors) * 0.99, 2), round(max(anchors) * 1.01, 2)]
        found, judgeable = divergence_summary(divergence)
        extra.update({"a_level": round(a_level, 4), "zone": zone, "divergence_found": found,
                      "divergence_judgeable": judgeable, "divergence": dict(divergence or {})})
        invalid_text = str(p["invalid_below"])
        if p.get("invalid_ma"):
            n, buffer = int(p["invalid_ma"]), float(p.get("invalid_buffer_pct", 3.0))
            extra.update({"invalid_ma": n, "invalid_buffer_pct": buffer})
            invalid_text = f"当日 MA{n}×{1 - buffer / 100:g}（昨 MA{n}={lv[f'ma{n}']}）"
        setup = f"收 {lv['close']} vs 短均线 {round(a_level, 2)}；A 目标前高 {p['prior_high']}；B 回踩区 {zone}"
        checklist = [f"A：放量站上当日 MA5/MA10 较高者（昨 {round(a_level, 2)}）+ 均价上方", f"B：进入 {zone} 且 30/60 分钟两段底背离",
                     f"收盘 < {invalid_text} 且无背离 → 失效"]
    elif playbook == "trend_continuation":
        hold, floor = f"ma{p.get('hold_ma', 5)}", f"ma{p.get('floor_ma', 10)}"
        extra.update({"hold_level": lv[hold], "floor_level": lv[floor],
                      "hold_ma": int(p.get("hold_ma", 5)), "floor_ma": int(p.get("floor_ma", 10))})
        setup = f"收 {lv['close']}，{hold.upper()} {lv[hold]}，前高 {p['prior_high']}"
        checklist = [f"持有：价格 ≥ {hold.upper()}={lv[hold]}", f"延续：价格 > 前高 {p['prior_high']}", f"跌破 {floor.upper()}={lv[floor]} → 失效"]
    elif playbook == "prior_high_breakout":
        floor = f"ma{p.get('floor_ma', 10)}"
        extra.update({"floor_level": lv[floor], "floor_ma": int(p.get("floor_ma", 10))})
        setup = f"收 {lv['close']}，距前高 {p['prior_high']} 差 {round((p['prior_high'] / lv['close'] - 1) * 100, 2)}%"
        checklist = [f"带量突破前高 {p['prior_high']} + 均价上方", f"跌破 {floor.upper()}={lv[floor]} → 失效"]
    elif playbook == "platform_breakout":
        floor_ma = int(p.get("floor_ma", 10))
        extra.update({"platform_upper": lv["platform_upper"], "platform_lower": lv["platform_lower"],
                      "ma10": lv["ma10"], "days_since_peak": lv["days_since_peak"], "floor_ma": floor_ma,
                      f"ma{floor_ma}": lv[f"ma{floor_ma}"]})
        setup = (f"仍在创新高（{lv['peak20']}），等回调走平台" if lv["days_since_peak"] == 0 else
                 f"高点 {lv['peak20']}@{lv['peak20_date'][4:]} 后整理 {lv['days_since_peak']} 天，平台 [{lv['platform_lower']}, {lv['platform_upper']}]")
        floor_level = lv[f"ma{floor_ma}"] or lv["platform_lower"]
        checklist = [f"带量突破平台上沿 {lv['platform_upper']} + 均价上方",
                     f"收盘跌破 平台下沿 {lv['platform_lower']} 与当日 MA{floor_ma}（昨 {floor_level}）较低者 → 失效"]
    elif playbook == "ma10_second_wave":
        touched = lv["low"] <= (lv["ma10"] or 0) * (1 + float(p["touch_tol_pct"]) / 100)
        extra.update({"ma10": lv["ma10"], "touched_ma10": touched, "prior_mid": lv["mid"]})
        setup = f"{'已到' if touched else '未到'}10日线：最低 {lv['low']} vs MA10 {lv['ma10']}"
        checklist = [f"回到 MA10（≤{round((lv['ma10'] or 0) * 1.01, 2)}）", f"重心不再下移（日变动 ≥ -{DEFAULTS['center_flat_pct']:g}%）+ 均价上方",
                     f"跌破 MA10×0.97 → 失效"]
    elif playbook == "double_bottom_platform":
        setup = f"收 {lv['close']} {'>' if lv['close'] > p['neckline'] else '≤'} 颈线 {p['neckline']}"
        checklist = [f"守颈线 {p['neckline']}", f"带量突破 {p['platform_high']}（节奏慢）", "基本面待确认"]
    elif playbook == "ma60_reclaim":
        extra.update({"ma60": lv["ma60"], "amt20": lv["amt20"],
                      "prev_close_below_ma60": bool(lv["ma60"]) and lv["close"] < lv["ma60"]})
        setup = f"收 {lv['close']} vs MA60 {lv['ma60']}；20日均额 {_yi(lv['amt20'] or 0)}"
        checklist = [f"站上 MA60={lv['ma60']} 且全天成交 ≥ {p['amount_mult']:g}×20日均额", "跌破 MA60×0.97 → 失效"]
    levels = {key: lv[key] for key in ("date", "close", "high", "low", "amount", "ma5", "ma10", "ma20", "ma60")}
    return {"levels": levels, "extra": extra, "setup": setup, "checklist": checklist}


__all__ = ["DIVERGENCE_MIN_BARS", "bullish_divergence", "daily_levels", "divergence_status", "divergence_summary",
           "limit_up_price", "parse_longhu_kline", "plan_stock", "sma"]
