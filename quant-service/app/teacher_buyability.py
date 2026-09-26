"""Was a flagged name actually buyable that session, and if so when.

``strategy_outcome_measures`` rightly refuses to credit an entry taken at the
limit price: there is no seller there.  But "unbuyable" then covers two very
different facts, and the 2026-09-24 session showed both in the same report:

* 新华文轩 opened at the limit after the 09:25 auction already printed +10 %
  and stayed there for all 241 minutes - **no counterparty existed all day**,
  so nothing about our marking could have helped;
* 天威视讯 opened at 8.30 with the limit at 8.43, broke the board three times
  and traded below the limit for 17 minutes carrying 42 % of the day's volume.
  That one we simply marked too late.

Collapsing the two hides the only actionable half.  This module separates them
from the stored minute tape, and applies the teacher's own filter while doing
it: he buys above the session VWAP, so a minute below the limit but under VWAP
is not an entry he would have taken.  On 2026-09-24 that distinction mattered -
天威视讯's 10:32-10:43 window sat below VWAP throughout, and only 13:04-13:07
(8.34-8.38 against VWAP 8.309) was an entry his rules allow.

Pure: the caller supplies the minute rows and the limit price.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from .strategy_outcome_measures import SEALED_TOLERANCE

#: What the verdict can be, and what each one means for a review.
VERDICTS = {
    "one_word": "一字板：全时段无低于涨停价的成交，盘中无法买入（只能竞价挂单）",
    "late_mark": "有低于涨停价且在分时均价上方的窗口，标记太晚",
    "below_vwap_only": "有低于涨停价的窗口，但全部在分时均价下方，按老师规则本就不该买",
    "no_limit_touch": "当日未触及涨停价，不属于买不到",
    "unknown": "没有分钟证据，无法判断",
}


def _num(value: Any) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _minute(row: Mapping[str, Any]) -> str:
    for key in ("minute_bucket", "minute", "time"):
        value = row.get(key)
        if value:
            return str(value)[-5:]
    return ""


def share_multiplier(rows: Sequence[Mapping[str, Any]]) -> float:
    """Shares per unit of ``volume``: 100 when the tape counts lots, else 1.

    A-share minute rows arrive with volume in 手 (lots) and amount in yuan, so
    ``amount / volume`` is 100x the price and every minute looks like it traded
    below VWAP.  That is not a theoretical risk: the first run of this module on
    2026-09-24 called 天威视讯 and 奥佳华 ``below_vwap_only`` for exactly this
    reason.  The unit is inferred from the rows themselves rather than assumed,
    because a different source may already report shares.
    """
    for row in rows:
        close, volume, amount = _num(row.get("close")), _num(row.get("volume")), _num(row.get("amount"))
        if not close or not volume or not amount or close <= 0:
            continue
        implied = amount / volume
        if implied <= 0:
            continue
        return 100.0 if implied > close * 10 else 1.0
    return 1.0


def buyability(rows: Sequence[Mapping[str, Any]], *, limit_up_price: Any,
               tolerance: float = SEALED_TOLERANCE) -> dict[str, Any]:
    """Split "could not be bought" into "no seller" and "we were late".

    ``rows`` are one session's minute bars in time order, each with a close and
    (for the VWAP filter) volume and amount.  VWAP is accumulated from the same
    rows rather than taken from a quote, so it is the tape's own average; the
    volume unit is inferred by :func:`share_multiplier`.
    """
    limit = _num(limit_up_price)
    usable = [row for row in rows if _num(row.get("close")) is not None]
    if not usable or limit is None or limit <= 0:
        return {"verdict": "unknown", "minutes": len(usable), "reason": "no_minute_rows"
                if not usable else "no_limit_price"}
    touched = any(_num(row["close"]) >= limit - tolerance for row in usable)
    if not touched:
        return {"verdict": "no_limit_touch", "minutes": len(usable),
                "highest_close": round(max(_num(row["close"]) for row in usable), 3)}

    shares_per_unit = share_multiplier(usable)
    cumulative_amount = cumulative_volume = 0.0
    below: list[dict[str, Any]] = []
    compliant: list[dict[str, Any]] = []
    volume_below = volume_all = 0.0
    for row in usable:
        close = _num(row["close"])
        volume = _num(row.get("volume")) or 0.0
        amount = _num(row.get("amount")) or 0.0
        cumulative_volume += volume * shares_per_unit
        cumulative_amount += amount
        vwap = cumulative_amount / cumulative_volume if cumulative_volume > 0 and cumulative_amount > 0 else None
        volume_all += volume
        if close >= limit - tolerance:
            continue
        volume_below += volume
        sample = {"at": _minute(row), "price": round(close, 3),
                  "vwap": round(vwap, 3) if vwap else None,
                  "above_vwap": None if vwap is None else close >= vwap,
                  "discount_to_limit_pct": round((1 - close / limit) * 100, 2)}
        below.append(sample)
        if sample["above_vwap"]:
            compliant.append(sample)

    if not below:
        # ``volume_unit`` 在这一支也要写：下游要靠它判断成交量是手还是股，
        # 少了它一字板那条记录就没法和其他判定放在一起比。
        return {"verdict": "one_word", "minutes": len(usable), "minutes_below_limit": 0,
                "volume_unit": "lot" if shares_per_unit == 100.0 else "share",
                "volume_share_below_pct": 0.0, "compliant_minutes": 0,
                "note": "全时段封死，无对手盘"}
    result = {
        "minutes": len(usable), "minutes_below_limit": len(below),
        "volume_unit": "lot" if shares_per_unit == 100.0 else "share",
        "volume_share_below_pct": round(volume_below / volume_all * 100, 1) if volume_all else None,
        "cheapest_below_limit": min(sample["price"] for sample in below),
        "last_below_limit_at": below[-1]["at"],
        "compliant_minutes": len(compliant),
        "verdict": "late_mark" if compliant else "below_vwap_only",
    }
    if compliant:
        best = max(compliant, key=lambda sample: sample["discount_to_limit_pct"])
        result.update({"first_compliant_at": compliant[0]["at"],
                       "first_compliant_price": compliant[0]["price"],
                       "best_compliant_price": best["price"],
                       "best_compliant_discount_pct": best["discount_to_limit_pct"],
                       "compliant_window": [sample["at"] for sample in compliant[:8]]})
    return result


def buyability_line(diagnosis: Mapping[str, Any] | None) -> str | None:
    """One human line for the report; ``None`` when there is nothing to say."""
    if not diagnosis:
        return None
    verdict = str(diagnosis.get("verdict") or "unknown")
    if verdict == "one_word":
        return f"一字板，{diagnosis.get('minutes')} 分钟全程封死，无对手盘（不是标记太晚）"
    if verdict == "late_mark":
        return (f"可买：低于涨停 {diagnosis.get('minutes_below_limit')} 分钟（占成交 "
                f"{diagnosis.get('volume_share_below_pct')}%），其中均价上方 {diagnosis.get('compliant_minutes')} 分钟，"
                f"最早 {diagnosis.get('first_compliant_at')} @ {diagnosis.get('first_compliant_price')}"
                f"，最优距涨停 -{diagnosis.get('best_compliant_discount_pct')}% — 标记太晚")
    if verdict == "below_vwap_only":
        return (f"低于涨停 {diagnosis.get('minutes_below_limit')} 分钟但全在分时均价下方"
                f"（最低 {diagnosis.get('cheapest_below_limit')}），按老师规则本就不该买")
    if verdict == "no_limit_touch":
        return None
    return "无分钟证据，买得到买不到未核实"


__all__ = ["VERDICTS", "buyability", "buyability_line", "share_multiplier"]
