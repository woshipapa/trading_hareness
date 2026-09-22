"""Post-close review of every watched stock: its day, its sector, its history, its patterns.

For each stock on the watchlist this produces one structured, stored record:

* the day - gap, OHLC, amount against its 20-day average, turnover;
* the intraday path - when the high and low printed, the share of minutes
  above the session VWAP, morning/afternoon/last-30-minute moves, the
  drawdown from the high, where it closed in its range;
* limit-up behaviour - first seal, board openings, sealed at the close;
* its industry - the industry board's move, the stock's relative strength,
  the correlation of their 5-minute changes and who led whom;
* recent history - 5/20-day moves, position in the 60-day range, MA order,
  limit-ups in 20 sessions;
* the scan tape - the opening auction (09:15/09:20/09:25), seal on/off at
  scan resolution, volume-ratio and sector-breadth checkpoints, minute
  volume bursts, when the stock and its peer group peaked, the signal
  timeline, and the tape's own coverage;
* the 09:25 opening auction from the data plane - matched amount, the
  unmatched buy/sell volume (a one-word board's seal), auction change %,
  turnover - accepted only when final, on the day's previous close and at
  the day's open (the snapshot itself carries no date);
* the day's signals, and transparent pattern labels.

The same run stores each watched stock's full session of 1-minute bars
(``intraday_minute_sessions``, Longhu) so later reviews can re-derive
features from the raw minutes.

Records accumulate day by day so later reviews can mine recurring patterns
(for example which intraday shapes, sector relations or limit behaviour
precede which next-day outcomes).  Pure functions here; the composition root
supplies the data-plane reads.  Research only - it never feeds a live rule.
"""

from __future__ import annotations

import math
from datetime import date, datetime
from statistics import mean, median
from typing import Any, Iterable, Mapping

REVIEW_VERSION = "watch-daily-review-v1"
REVIEW_CAPABILITY = "watch_daily_review"
REVIEW_PROVIDER = "quant_scan"
LEAD_LAG_MIN_POINTS = 24


def _num(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _minutes_since_open(hhmm: str) -> int:
    hour, minute = int(hhmm[:2]), int(hhmm[2:4])
    total = hour * 60 + minute
    return total - 570 if total <= 690 else 120 + max(0, total - 780)


def _clock(hhmm: str) -> str:
    return f"{hhmm[:2]}:{hhmm[2:4]}"


def _corr(left: list[float], right: list[float]) -> float | None:
    if len(left) < 6 or len(left) != len(right):
        return None
    ml, mr = mean(left), mean(right)
    cov = sum((a - ml) * (b - mr) for a, b in zip(left, right))
    vl = sum((a - ml) ** 2 for a in left)
    vr = sum((b - mr) ** 2 for b in right)
    return round(cov / math.sqrt(vl * vr), 3) if vl > 0 and vr > 0 else None


def intraday_path(minutes: list[Mapping[str, Any]], pre_close: float | None) -> dict[str, Any]:
    """Shape of the session from today's 1-minute rows (close, cumulative VWAP, volume)."""
    rows = [row for row in minutes if _num(row.get("close"))]
    if len(rows) < 10:
        return {"status": "insufficient_minutes", "minutes": len(rows)}
    closes = [float(row["close"]) for row in rows]
    times = [str(row.get("time") or "") for row in rows]
    high_i = max(range(len(closes)), key=lambda i: closes[i])
    low_i = min(range(len(closes)), key=lambda i: closes[i])
    high, low, last, first = closes[high_i], closes[low_i], closes[-1], closes[0]
    above = [c >= float(v) for c, v in ((c, row.get("vwap")) for c, row in zip(closes, rows)) if _num(v)]

    def close_at(limit: str) -> float | None:
        eligible = [c for c, t in zip(closes, times) if t <= limit]
        return eligible[-1] if eligible else None

    base = pre_close or first
    noon, last30 = close_at("1130"), close_at("1430")
    volumes = [_num(row.get("volume_lot")) or 0.0 for row in rows]
    total_volume = sum(volumes) or None
    first30 = sum(v for v, t in zip(volumes, times) if t <= "1000")
    return {
        "status": "ok", "minutes": len(rows),
        "high_time": _clock(times[high_i]), "low_time": _clock(times[low_i]),
        "high_first": high_i < low_i,
        "pct_minutes_above_vwap": round(100 * sum(above) / len(above), 1) if above else None,
        "close_vs_vwap_pct": round((last / float(rows[-1]["vwap"]) - 1) * 100, 2) if _num(rows[-1].get("vwap")) else None,
        "morning_pct": round((noon / base - 1) * 100, 2) if noon and base else None,
        "afternoon_pct": round((last / noon - 1) * 100, 2) if noon else None,
        "last30_pct": round((last / last30 - 1) * 100, 2) if last30 else None,
        "drawdown_from_high_pct": round((last / high - 1) * 100, 2),
        "rebound_from_low_pct": round((last / low - 1) * 100, 2),
        "close_range_position": round((last - low) / (high - low), 3) if high > low else 1.0,
        "first30_volume_share": round(first30 / total_volume, 3) if total_volume else None,
    }


def sector_relation(minutes: list[Mapping[str, Any]], pre_close: float | None,
                    sector_series: list[tuple[str, float]], sector_label: str | None) -> dict[str, Any]:
    """Stock vs its industry board on a common 5-minute grid of cumulative change %."""
    if not sector_series or not pre_close:
        return {"status": "no_sector_series", "label": sector_label}
    stock = {}
    for row in minutes:
        price = _num(row.get("close"))
        if price:
            stock[str(row.get("time"))[:4]] = (price / pre_close - 1) * 100
    grid = sorted({t for t, _ in sector_series if t in stock and _minutes_since_open(t) % 5 == 0})
    sector = dict(sector_series)
    if len(grid) < 8:
        return {"status": "sparse_overlap", "label": sector_label, "points": len(grid)}
    s = [stock[t] for t in grid]
    b = [sector[t] for t in grid]
    ds = [s2 - s1 for s1, s2 in zip(s, s[1:])]
    db = [b2 - b1 for b1, b2 in zip(b, b[1:])]
    same = _corr(ds, db)
    stock_leads = _corr(ds[:-1], db[1:])
    sector_leads = _corr(ds[1:], db[:-1])
    lead = None
    # Lead/lag needs about two hours of 5-minute points to mean anything.
    if len(grid) >= LEAD_LAG_MIN_POINTS and stock_leads is not None and sector_leads is not None:
        if stock_leads - sector_leads >= 0.25:
            lead = "stock_leads"
        elif sector_leads - stock_leads >= 0.25:
            lead = "sector_leads"
        else:
            lead = "synchronous"
    return {
        "status": "ok", "label": sector_label, "points": len(grid),
        "sector_close_pct": round(b[-1], 2), "stock_close_pct": round(s[-1], 2),
        "relative_strength_pct": round(s[-1] - b[-1], 2),
        "corr_5m": same, "corr_stock_leads": stock_leads, "corr_sector_leads": sector_leads, "lead": lead,
    }


def history_context(bars: list[Mapping[str, Any]]) -> dict[str, Any]:
    """Recent and 60-day context from split-adjusted daily bars ending today."""
    if len(bars) < 21:
        return {"status": "insufficient_history", "bars": len(bars)}
    closes = [float(bar["close"]) for bar in bars]
    amounts = [float(bar.get("amount") or 0) for bar in bars]
    window = bars[-60:]
    high60 = max(float(bar["high"]) for bar in window)
    low60 = min(float(bar["low"]) for bar in window)
    ma = {n: sum(closes[-n:]) / n for n in (5, 10, 20) if len(closes) >= n}
    return {
        "status": "ok",
        "pct_5d": round((closes[-1] / closes[-6] - 1) * 100, 2),
        "pct_20d": round((closes[-1] / closes[-21] - 1) * 100, 2),
        "amount_vs_20d": round(amounts[-1] / mean(amounts[-21:-1]), 2) if mean(amounts[-21:-1]) else None,
        "range60_position": round((closes[-1] - low60) / (high60 - low60), 3) if high60 > low60 else None,
        "at_60d_high": closes[-1] >= high60 * 0.995,
        "ma_bullish_order": ma.get(5, 0) > ma.get(10, 0) > ma.get(20, 0),
        "limit_ups_20d": sum(1 for bar in bars[-20:] if bar.get("limit_up")),
    }


TAPE_CHECKPOINTS = ("0915", "0920", "0925", "0935", "1000", "1030", "1130", "1330", "1400", "1430", "1500")
VOLUME_BURST_MULTIPLE = 3.0


def _bucket5(hhmmss: str) -> str | None:
    """The 5-minute bucket (by minutes since 09:30) of a continuous-session time."""
    if not ("0930" <= hhmmss[:4] <= "1130" or "1300" <= hhmmss[:4] <= "1500"):
        return None
    return str(_minutes_since_open(hhmmss[:4]) // 5)


def tape_timeline(entries: list[tuple[str, Mapping[str, Any]]]) -> dict[str, Any]:
    """One stock's day from its scan tape: ``entries`` are (HHMMSS, tape row) in time order."""
    rows = [(stamp, row) for stamp, row in entries if isinstance(row, Mapping) and _num(row.get("p"))]
    if not rows:
        return {"status": "no_tape", "scans": len(entries)}
    live = [(stamp, row) for stamp, row in rows if stamp[:4] >= "0930"]

    def at(check: str) -> Mapping[str, Any] | None:
        # The last scan at or before the checkpoint, within the same half-session.
        floor = "0915" if check < "0930" else "0930" if check <= "1130" else "1300"
        eligible = [row for stamp, row in rows if floor <= stamp[:4] and stamp[:4] + stamp[4:6] <= check + "00"]
        return eligible[-1] if eligible else None

    checkpoints = {}
    for check in TAPE_CHECKPOINTS:
        row = at(check)
        if row is not None:
            values = {"pct": row.get("pct"), "vr": row.get("vr"), "gb": (row.get("sec") or {}).get("gb"),
                      "sealed": row.get("sealed")}
            checkpoints[_clock(check)] = {key: value for key, value in values.items() if value is not None}
    auction = {}
    for check in ("0915", "0920", "0925"):
        row = at(check) if rows[0][0][:4] < "0930" else None
        if row is not None and row.get("pct") is not None:
            auction[_clock(check)] = row["pct"]
    if "09:20" in auction and "09:25" in auction:
        auction["after_0920_change"] = round(auction["09:25"] - auction["09:20"], 2)

    # Seal episodes from continuous-session scans; a one-scan unsealed flicker
    # (a missing bid book, a source switch) is not counted as a board opening.
    seal_states = [(stamp, bool(row["sealed"])) for stamp, row in live if row.get("sealed") is not None]
    episodes, flickers, start, gap = [], 0, None, 0
    for index, (stamp, sealed) in enumerate(seal_states):
        if sealed:
            if start is None:
                start = stamp
            elif gap == 1:
                flickers += 1
            gap = 0
        elif start is not None:
            gap += 1
            if gap == 2:
                episodes.append((start, seal_states[index - 2][0]))
                start, gap = None, 0
    if start is not None:
        episodes.append((start, seal_states[-1][0]))
    touched = [stamp for stamp, row in live if row.get("touched")]
    seal = {
        "first_touch": _clock(touched[0]) if touched else None,
        "first_seal": _clock(episodes[0][0]) if episodes else None,
        "episodes": [[_clock(a), _clock(b)] for a, b in episodes][:12],
        "breaks": max(0, len(episodes) - 1) + (1 if episodes and seal_states and not seal_states[-1][1] else 0),
        "flickers": flickers,
        "sealed_share_after_first_seal": round(sum(1 for stamp, sealed in seal_states if sealed and stamp >= episodes[0][0])
                                               / max(1, sum(1 for stamp, _ in seal_states if stamp >= episodes[0][0])), 3)
        if episodes else None,
    } if seal_states else {}

    ratios = [(stamp, _num(row.get("vr"))) for stamp, row in live if _num(row.get("vr")) is not None]
    peak_vr = max(ratios, key=lambda item: item[1]) if ratios else None
    above = [float(row["p"]) >= float(row["vw"]) for _, row in live if _num(row.get("vw"))]
    bursts: dict[str, tuple[float, float | None]] = {}
    for stamp, row in live:
        minute = row.get("m") or {}
        multiple, when = _num(minute.get("vm")), str(minute.get("t") or stamp[:4])[:4]
        if multiple is not None and multiple >= VOLUME_BURST_MULTIPLE and multiple > bursts.get(when, (0.0, None))[0]:
            bursts[when] = (multiple, _num(minute.get("r1")))
    top_bursts = sorted(bursts.items(), key=lambda item: -item[1][0])[:5]

    pct_live = [(stamp, float(row["pct"])) for stamp, row in live if _num(row.get("pct")) is not None]
    breadth = [(stamp, float(row["sec"]["gb"])) for stamp, row in live
               if isinstance(row.get("sec"), Mapping) and _num(row["sec"].get("gb")) is not None]
    groups = [str(row["sec"].get("g")) for _, row in live if isinstance(row.get("sec"), Mapping) and row["sec"].get("g")]
    peer: dict[str, Any] = {}
    if breadth and pct_live:
        stock_peak = max(pct_live, key=lambda item: item[1])[0]
        breadth_peak = max(breadth, key=lambda item: item[1])
        stock_b, breadth_b = {}, {}
        for stamp, value in pct_live:
            if _bucket5(stamp) is not None:
                stock_b[_bucket5(stamp)] = value
        for stamp, value in breadth:
            if _bucket5(stamp) is not None:
                breadth_b[_bucket5(stamp)] = value
        grid = sorted(set(stock_b) & set(breadth_b), key=int)
        ds = [stock_b[b] - stock_b[a] for a, b in zip(grid, grid[1:])]
        dg = [breadth_b[b] - breadth_b[a] for a, b in zip(grid, grid[1:])]
        peer = {
            "group": max(set(groups), key=groups.count) if groups else None,
            "breadth_open": breadth[0][1], "breadth_peak": breadth_peak[1], "breadth_peak_time": _clock(breadth_peak[0]),
            "breadth_close": breadth[-1][1], "stock_peak_time": _clock(stock_peak),
            "breadth_peaked_first": breadth_peak[0] < stock_peak,
            "corr_5m_stock_vs_breadth": _corr(ds, dg),
        }

    first_seen: dict[str, str] = {}
    for stamp, row in rows:
        for item in row.get("sig") or []:
            key = ":".join(str(item).split(":")[:2])
            first_seen.setdefault(key, _clock(stamp))
    fresh = [str(row.get("fresh")) for _, row in live if row.get("fresh")]
    return {
        "status": "ok", "scans": len(rows), "continuous_scans": len(live),
        "first_scan": _clock(rows[0][0]), "last_scan": _clock(rows[-1][0]),
        "auction": auction, "checkpoints": checkpoints, "seal": seal,
        "volume_ratio_peak": {"value": peak_vr[1], "time": _clock(peak_vr[0])} if peak_vr else None,
        "pct_scans_above_vwap": round(100 * sum(above) / len(above), 1) if above else None,
        "volume_bursts": [{"time": _clock(when), "multiple": round(multiple, 2), "return_1m_pct": r1}
                          for when, (multiple, r1) in top_bursts],
        "volume_burst_minutes": len(bursts),
        "peer_group": peer,
        "signal_first_seen": first_seen,
        "stale_share": round(sum(1 for status in fresh if status != "fresh") / len(fresh), 3) if fresh else None,
    }


def auction_facts(raw: Mapping[str, Any] | None, quote_day: Mapping[str, Any]) -> dict[str, Any]:
    """The opening auction from the Fuyao snapshot, validated against the day itself."""
    if not raw:
        return {"status": "missing"}
    if str(raw.get("data_status") or "") != "final":
        return {"status": "not_final", "data_status": raw.get("data_status")}
    pre_close, opened = _num(quote_day.get("pre_close")), _num(quote_day.get("open"))
    snapshot_pre_close, price = _num(raw.get("pre_close_price")), _num(raw.get("auction_price"))
    if pre_close is None or snapshot_pre_close is None or abs(snapshot_pre_close - pre_close) > max(0.011, pre_close * 0.001):
        return {"status": "rejected", "reason": "pre_close_mismatch", "snapshot_pre_close": snapshot_pre_close,
                "day_pre_close": pre_close}
    if opened is not None and price is not None and abs(price - opened) > 0.006:
        return {"status": "rejected", "reason": "not_at_the_day_open", "auction_price": price, "day_open": opened}
    unmatched = _num(raw.get("auction_unmatched"))
    limit_price = _num(quote_day.get("limit_up_price"))
    return {
        "status": "ok", "price": price, "pct": _num(raw.get("auction_pct")), "amount": _num(raw.get("auction_amount")),
        "volume_lot": _num(raw.get("auction_volume")), "turnover_pct": _num(raw.get("auction_turnover_pct")),
        "volume_ratio": _num(raw.get("auction_volume_ratio")), "vs_yesterday_pct": _num(raw.get("auction_yesterday_ratio_pct")),
        # unmatched lots: positive = unfilled buy volume (the seal), negative = unfilled sell
        "unmatched_lot": unmatched,
        "unmatched_amount": round(unmatched * 100 * price, 2) if unmatched is not None and price else None,
        "at_limit_up": bool(limit_price and price and price >= limit_price - 0.005),
    }


def pattern_labels(day: Mapping[str, Any], path: Mapping[str, Any], limit: Mapping[str, Any],
                   sector: Mapping[str, Any], history: Mapping[str, Any],
                   tape: Mapping[str, Any] | None = None) -> list[str]:
    """Transparent, rule-based labels (thresholds are defaults, recorded with the review)."""
    labels: list[str] = []
    gap = _num(day.get("gap_pct"))
    if day.get("one_word_board"):
        labels.append("一字板")
    elif gap is not None:
        labels.append("高开" if gap >= 2 else "低开" if gap <= -2 else "平开")
    if path.get("status") == "ok":
        position, drawdown = path["close_range_position"], path["drawdown_from_high_pct"]
        above, last30 = path.get("pct_minutes_above_vwap"), path.get("last30_pct")
        if path["high_first"] and position < 0.4 and drawdown <= -3:
            labels.append("冲高回落")
        if gap is not None and gap <= -1 and (_num(day.get("close_pct")) or 0) - gap >= 3:
            labels.append("低开高走")
        if last30 is not None and last30 >= 2:
            labels.append("尾盘拉升")
        if last30 is not None and last30 <= -2:
            labels.append("尾盘跳水")
        if position >= 0.9 and above is not None and above >= 70:
            labels.append("单边走强")
        if position <= 0.1 and above is not None and above <= 30:
            labels.append("单边走弱")
    if limit.get("sealed_at_close"):
        labels.append("封板" + ("（炸板回封）" if (limit.get("board_opens") or 0) > 0 else ""))
    elif limit.get("touched"):
        labels.append("冲板未封" if not limit.get("board_opens") else "炸板未回封")
    ratio = _num(history.get("amount_vs_20d"))
    if ratio is not None:
        labels.append("放量" if ratio >= 1.5 else "缩量" if ratio <= 0.7 else "平量")
    if sector.get("status") == "close_only":
        rs = sector["relative_strength_pct"]
        if rs >= 5:
            labels.append("强于板块")
        if (sector["stock_close_pct"] > 0) != (sector["sector_close_pct"] > 0) and abs(rs) >= 3:
            labels.append("逆板块")
    if sector.get("status") == "ok":
        rs, corr = sector["relative_strength_pct"], sector.get("corr_5m")
        if rs >= 5 and corr is not None and corr >= 0.4:
            labels.append("板块领涨")
        elif corr is not None and corr >= 0.6:
            labels.append("跟随板块")
        elif corr is not None and corr < 0.2:
            labels.append("独立于板块")
        if (sector["stock_close_pct"] > 0) != (sector["sector_close_pct"] > 0) and abs(rs) >= 3:
            labels.append("逆板块")
        if sector.get("lead") == "stock_leads":
            labels.append("领先板块")
    if history.get("at_60d_high"):
        labels.append("60日新高")
    tape = tape or {}
    change = _num((tape.get("auction") or {}).get("after_0920_change"))
    if change is not None and abs(change) >= 1:
        labels.append("竞价走强" if change > 0 else "竞价走弱")
    if ((tape.get("seal") or {}).get("breaks") or 0) >= 2:
        labels.append("反复炸板")
    if (day.get("auction") or {}).get("at_limit_up"):
        labels.append("竞价涨停")
    peer = tape.get("peer_group") or {}
    if peer.get("breadth_peaked_first") and path.get("status") == "ok" and path.get("drawdown_from_high_pct", 0) <= -3:
        labels.append("板块先退潮")
    return labels


def review_stock(*, symbol: str, name: str, trade_date: date, quote_day: Mapping[str, Any],
                 minutes: list[Mapping[str, Any]], bars: list[Mapping[str, Any]],
                 limit: Mapping[str, Any], sector: Mapping[str, Any], sector_series: list[tuple[str, float]],
                 signals: list[Mapping[str, Any]], themes: Iterable[str] = (),
                 tape: list[tuple[str, Mapping[str, Any]]] = (),
                 auction: Mapping[str, Any] | None = None) -> dict[str, Any]:
    pre_close = _num(quote_day.get("pre_close"))
    opened, high, low, close = (_num(quote_day.get(key)) for key in ("open", "high", "low", "close"))
    limit_price = _num(quote_day.get("limit_up_price"))
    day = {
        "open": opened, "high": high, "low": low, "close": close, "pre_close": pre_close,
        "gap_pct": round((opened / pre_close - 1) * 100, 2) if opened and pre_close else None,
        "close_pct": round((close / pre_close - 1) * 100, 2) if close and pre_close else None,
        "amplitude_pct": round((high - low) / pre_close * 100, 2) if high and low and pre_close else None,
        "amount": _num(quote_day.get("amount")), "turnover_pct": _num(quote_day.get("turnover_pct")),
        "one_word_board": bool(limit_price and opened and high and low and opened == high == low
                               and abs(opened - limit_price) < 0.006),
    }
    day["auction"] = auction_facts(auction, quote_day)
    path = intraday_path(minutes, pre_close)
    relation = sector_relation(minutes, pre_close, sector_series, sector.get("label"))
    relation.update({key: sector.get(key) for key in ("taxonomy_key", "sector_key") if sector.get(key)})
    history = history_context(bars)
    timeline = tape_timeline(list(tape))
    limit = dict(limit)
    if timeline.get("seal"):
        # The tape sees every board opening; the pools only whether one happened.
        limit["seal_breaks_tape"] = timeline["seal"]["breaks"]
        limit["board_opens"] = max(int(limit.get("board_opens") or 0), timeline["seal"]["breaks"])
        limit["touched"] = bool(limit.get("touched") or timeline["seal"].get("first_touch"))
    return {
        "version": REVIEW_VERSION, "symbol": symbol, "name": name, "trade_date": trade_date.isoformat(),
        "day": day, "path": path, "limit": limit, "sector": relation, "history": history, "tape": timeline,
        "themes": sorted({str(theme) for theme in themes if theme})[:12],
        "signals": [dict(item) for item in signals][:30],
        "patterns": pattern_labels(day, path, limit, relation, history, timeline),
    }


def summarize(reviews: list[Mapping[str, Any]]) -> dict[str, Any]:
    """Cross-stock view of one day: pattern counts and industries that moved together."""
    patterns: dict[str, list[str]] = {}
    sectors: dict[str, list[tuple[str, float | None]]] = {}
    for review in reviews:
        for label in review.get("patterns") or []:
            patterns.setdefault(label, []).append(review["name"])
        sector = review.get("sector") or {}
        if sector.get("label"):
            sectors.setdefault(str(sector["label"]), []).append((review["name"], sector.get("relative_strength_pct")))
    return {
        "stocks": len(reviews),
        "patterns": {label: {"count": len(names), "stocks": names[:10]} for label, names in
                     sorted(patterns.items(), key=lambda item: -len(item[1]))},
        "industries": {label: {"stocks": [name for name, _ in members],
                               "median_relative_strength": median([v for _, v in members if v is not None])
                               if any(v is not None for _, v in members) else None}
                       for label, members in sorted(sectors.items(), key=lambda item: -len(item[1]))},
    }


__all__ = ["REVIEW_CAPABILITY", "REVIEW_PROVIDER", "REVIEW_VERSION", "history_context", "intraday_path",
           "auction_facts", "pattern_labels", "review_stock", "sector_relation", "summarize", "tape_timeline"]


# --- data-plane reads and orchestration --------------------------------------

import hashlib as _hashlib
import json as _json
from dataclasses import dataclass as _dataclass
from datetime import time as _time, timedelta as _timedelta
from typing import Awaitable as _Awaitable, Callable as _Callable
from zoneinfo import ZoneInfo as _ZoneInfo

from psycopg.types.json import Json as _Json

_CN = _ZoneInfo("Asia/Shanghai")


def _day_bounds(trade_date: date) -> tuple[datetime, datetime]:
    start = datetime.combine(trade_date, _time(0), tzinfo=_CN)
    return start, start + _timedelta(days=1)


def read_review_inputs(database: Any, trade_date: date) -> dict[str, Any]:
    """Everything the review needs from stored evidence for one session."""
    start, end = _day_bounds(trade_date)
    with database.transaction() as connection:
        watches = [dict(row) for row in connection.execute(
            "SELECT symbol,label,metadata FROM quant.intraday_watchlists WHERE enabled ORDER BY symbol").fetchall()]
        symbols = [row["symbol"] for row in watches]
        quotes = {row["symbol"]: dict(row) for row in connection.execute(
            """SELECT DISTINCT ON (symbol) symbol,observed_at,price,pct_change,volume_ratio,turnover_rate,raw
                 FROM quant.intraday_quote_observations
                WHERE symbol=ANY(%s) AND observed_at>=%s AND observed_at<%s
                ORDER BY symbol,observed_at DESC""", (symbols, start, end)).fetchall()}
        industries: dict[str, dict[str, Any]] = {}
        for row in connection.execute(
                """SELECT symbol,sector_key,raw FROM quant.sector_membership_history
                    WHERE symbol=ANY(%s) AND taxonomy_key='longhu_ths_industry' AND effective_to IS NULL""",
                (symbols,)).fetchall():
            vendor = ((row["raw"] or {}).get("raw") or {}).get("vendor_row") or []
            industries.setdefault(row["symbol"], {"taxonomy_key": "longhu_ths_industry", "sector_key": str(row["sector_key"]),
                                                  "concepts": str(vendor[4]) if len(vendor) > 4 and vendor[4] else ""})
        boards: dict[str, list[tuple[str, float]]] = {}
        labels: dict[str, str] = {}
        for row in connection.execute(
                """SELECT snapshot_minute,payload FROM quant.intraday_board_flow_snapshots
                    WHERE observed_at>=%s AND observed_at<%s ORDER BY snapshot_minute""", (start, end)).fetchall():
            stamp = row["snapshot_minute"].astimezone(_CN).strftime("%H%M")
            for item in (row["payload"] or {}).get("items") or []:
                if item.get("taxonomy_key") == "longhu_ths_industry" and _num(item.get("change_pct")) is not None:
                    key = str(item.get("sector_key"))
                    boards.setdefault(key, []).append((stamp, float(item["change_pct"])))
                    labels[key] = str(item.get("label") or key)
        pool_last: dict[str, dict[str, Any]] = {}
        opened: set[str] = set()
        for row in connection.execute(
                """SELECT DISTINCT ON (symbol,event_type) symbol,event_type,body FROM quant.market_events
                    WHERE symbol=ANY(%s) AND source='fuyao_ths' AND event_type IN ('limit_up_pool','limit_open_pool')
                      AND occurred_at>=%s AND occurred_at<%s ORDER BY symbol,event_type,occurred_at DESC""",
                (symbols, start, end)).fetchall():
            if row["event_type"] == "limit_open_pool":
                opened.add(row["symbol"])
            else:
                pool_last[row["symbol"]] = _json.loads(row["body"]) if isinstance(row["body"], str) else dict(row["body"] or {})
        close_snapshot = connection.execute(
            """SELECT max(occurred_at) at FROM quant.market_events WHERE event_type='limit_up_pool' AND source='fuyao_ths'
                AND occurred_at>=%s AND occurred_at<%s""", (start, end)).fetchone()["at"]
        sealed_at_close = {row["symbol"] for row in connection.execute(
            """SELECT symbol FROM quant.market_events WHERE event_type='limit_up_pool' AND source='fuyao_ths'
                AND occurred_at=%s AND symbol=ANY(%s)""", (close_snapshot, symbols)).fetchall()} if close_snapshot else set()
        signals: dict[str, list[dict[str, Any]]] = {}
        for row in connection.execute(
                """SELECT e.symbol,e.signal_type,e.state,e.observed_at,e.conditions->>'setup' setup,
                          e.conditions->'teacher_review'->>'playbook' playbook, d.status delivery,
                          split_part(d.message_text, E'\\n', 1) title
                     FROM quant.intraday_signal_events e
                     LEFT JOIN quant.intraday_alert_deliveries d ON d.signal_event_id=e.signal_event_id
                    WHERE e.symbol=ANY(%s) AND e.observed_at>=%s AND e.observed_at<%s
                      AND e.state IN ('confirmed','alerted') ORDER BY e.observed_at""", (symbols, start, end)).fetchall():
            signals.setdefault(row["symbol"], []).append({
                "time": row["observed_at"].astimezone(_CN).strftime("%H:%M:%S"), "type": row["signal_type"],
                "state": row["state"], "setup": row["setup"], "playbook": row["playbook"],
                "delivery": row["delivery"], "title": row["title"]})
        previous_session = connection.execute(
            """SELECT max(calendar_date) d FROM quant.market_trade_calendar
                WHERE exchange='SSE' AND is_open AND calendar_date<%s""", (trade_date,)).fetchone()["d"]
    return {"watches": watches, "quotes": quotes, "industries": industries, "boards": boards, "board_labels": labels,
            "previous_session": previous_session,
            "pool_last": pool_last, "opened": opened, "sealed_at_close": sealed_at_close, "signals": signals}


def read_scan_tape(database: Any, trade_date: date) -> dict[str, list[tuple[str, dict[str, Any]]]]:
    """The day's ``watch_scan_tape`` rows regrouped per stock as (HHMMSS, row) in time order."""
    start, end = _day_bounds(trade_date)
    per_symbol: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    with database.transaction() as connection:
        for row in connection.execute(
                """SELECT effective_at, payload->'rows' AS rows FROM quant.raw_market_observations
                    WHERE provider_key='quant_scan' AND capability='watch_scan_tape' AND market='cn'
                      AND symbol='watch:scan' AND effective_at>=%s AND effective_at<%s ORDER BY effective_at""",
                (start, end)).fetchall():
            stamp = row["effective_at"].astimezone(_CN).strftime("%H%M%S")
            for symbol, item in (row["rows"] or {}).items():
                per_symbol.setdefault(symbol, []).append((stamp, item))
    return per_symbol


def persist_reviews(database: Any, trade_date: date, reviews: list[dict[str, Any]], summary: dict[str, Any]) -> int:
    effective = datetime.combine(trade_date, _time(15, 0), tzinfo=_CN)
    available = datetime.now(_CN)
    stored = 0
    with database.transaction() as connection:
        for symbol, payload in [(review["symbol"], review) for review in reviews] + [("watch:review", summary)]:
            body = {**payload, "provider_key": REVIEW_PROVIDER, "capability": REVIEW_CAPABILITY}
            serialized = _json.dumps(body, ensure_ascii=False, sort_keys=True, default=str)
            body = _json.loads(serialized)
            row = connection.execute(
                """INSERT INTO quant.raw_market_observations(provider_key,capability,market,symbol,effective_at,available_at,payload_sha256,normalized,payload)
                   VALUES(%s,%s,'cn',%s,%s,%s,%s,%s,%s)
                   ON CONFLICT(provider_key,capability,market,symbol,effective_at,payload_sha256) DO NOTHING
                   RETURNING observation_id""",
                (REVIEW_PROVIDER, REVIEW_CAPABILITY, symbol, effective, available,
                 _hashlib.sha256(serialized.encode()).hexdigest(), _Json(body), _Json(body)),
            ).fetchone()
            stored += 1 if row else 0
    return stored


def read_reviews(database: Any, trade_date: date) -> list[dict[str, Any]]:
    effective = datetime.combine(trade_date, _time(15, 0), tzinfo=_CN)
    with database.transaction() as connection:
        rows = connection.execute(
            """SELECT DISTINCT ON (symbol) symbol,payload FROM quant.raw_market_observations
                WHERE provider_key=%s AND capability=%s AND effective_at=%s ORDER BY symbol,available_at DESC""",
            (REVIEW_PROVIDER, REVIEW_CAPABILITY, effective)).fetchall()
    return [dict(row["payload"]) for row in rows]


@_dataclass(frozen=True)
class WatchReviewDependencies:
    database: Any
    run_database: _Callable[..., _Awaitable[Any]]
    minutes_batch: _Callable[[list[str]], _Awaitable[dict[str, Any]]]
    plan_bars: _Callable[..., dict[str, dict[str, Any]]]
    quote_features: _Callable[[str, Mapping[str, Any], datetime], dict[str, Any]]
    # Industry ranking now (104 boards, one licensed call): the fallback when the
    # minute board snapshots are missing (e.g. paused by the storage guard).
    industry_board_flow: _Callable[[], _Awaitable[list[dict[str, Any]]]] | None = None
    # symbols -> {symbol: raw Fuyao auction row}; the snapshot has no date, so
    # it is only asked for on the session's own day.
    auction_snapshot: _Callable[[list[str]], _Awaitable[dict[str, dict[str, Any]]]] | None = None
    # (trade_date, {symbol: minute rows}) -> storage report (intraday_minute_sessions)
    persist_minutes: _Callable[[date, dict[str, list[dict[str, Any]]]], _Awaitable[dict[str, Any]]] | None = None


async def run_watch_daily_review(trade_date: date, deps: WatchReviewDependencies, *, persist: bool = True) -> dict[str, Any]:
    inputs = await deps.run_database(lambda: read_review_inputs(deps.database, trade_date), timeout_seconds=90)
    symbols = [row["symbol"] for row in inputs["watches"]]
    try:
        tape = await deps.run_database(lambda: read_scan_tape(deps.database, trade_date), timeout_seconds=120)
    except Exception:  # noqa: BLE001 - a review without the tape keeps everything else
        tape = {}
    minutes = await deps.minutes_batch(symbols) if symbols else {}
    same_day = trade_date == datetime.now(_CN).date()
    auctions: dict[str, dict[str, Any]] = {}
    if same_day and symbols and deps.auction_snapshot is not None:
        try:
            auctions = await deps.auction_snapshot(symbols)
        except Exception:  # noqa: BLE001 - the review records the auction as missing
            auctions = {}
    minutes_stored: dict[str, Any] | None = None
    if persist and same_day and deps.persist_minutes is not None:
        whole = {symbol: rows for symbol, rows in minutes.items() if isinstance(rows, list) and rows}
        try:
            minutes_stored = await deps.persist_minutes(trade_date, whole) if whole else {"status": "empty"}
        except Exception as error:  # noqa: BLE001 - the review itself is still stored
            minutes_stored = {"status": "failed", "error": str(error)[:200]}
    bars = await deps.run_database(lambda: deps.plan_bars(deps.database, symbols, through=trade_date, limit=80),
                                   timeout_seconds=90)
    # Before the post-close daily sync today's bar does not exist yet: fall back
    # to history through the previous session (recorded as ``as_of``).
    missing = [symbol for symbol in symbols if (bars.get(symbol) or {}).get("status") != "ok"]
    if missing and inputs.get("previous_session"):
        earlier = await deps.run_database(
            lambda: deps.plan_bars(deps.database, missing, through=inputs["previous_session"], limit=80), timeout_seconds=90)
        for symbol, entry in earlier.items():
            if entry.get("status") == "ok":
                bars[symbol] = {**entry, "as_of": str(inputs["previous_session"])}
    reviews = []
    for watch in inputs["watches"]:
        symbol = watch["symbol"]
        meta = watch.get("metadata") or {}
        name = str((meta.get("teacher_review") or {}).get("name") or watch.get("label") or symbol).split("·")[0]
        row = inputs["quotes"].get(symbol)
        quote_day: dict[str, Any] = {}
        if row:
            quote = {"price": row["price"], "pct_change": row["pct_change"], "volume_ratio": row["volume_ratio"],
                     "turnover_rate": row["turnover_rate"], "raw": row["raw"] or {}}
            f = deps.quote_features(symbol, quote, row["observed_at"])
            quote_day = {"open": f.get("open"), "high": f.get("high"), "low": f.get("low"), "close": f.get("price"),
                         "pre_close": f.get("pre_close"), "amount": f.get("amount"), "turnover_pct": f.get("turnover_pct"),
                         "limit_up_price": f.get("limit_up_price")}
        pool = inputs["pool_last"].get(symbol) or {}
        limit_price = _num(quote_day.get("limit_up_price"))
        limit = {
            "touched": bool(pool) or symbol in inputs["opened"] or bool(
                limit_price and _num(quote_day.get("high")) and float(quote_day["high"]) >= limit_price - 0.005),
            "sealed_at_close": symbol in inputs["sealed_at_close"],
            "board_opens": 1 if symbol in inputs["opened"] else 0,
            "first_seal_time": pool.get("limit_up_time"), "streak": pool.get("continue_day_cnt"),
            "max_seal_amount": pool.get("max_seal_money"), "reason": pool.get("limit_up_reason"),
        }
        industry = inputs["industries"].get(symbol) or {}
        key = industry.get("sector_key")
        sector = {"taxonomy_key": industry.get("taxonomy_key"), "sector_key": key,
                  "label": inputs["board_labels"].get(key) if key else None}
        stock_minutes = minutes.get(symbol) if isinstance(minutes.get(symbol), list) else []
        entry = bars.get(symbol) or {}
        themes = [part for part in str(industry.get("concepts") or "").replace("，", "、").split("、") if part]
        themes += [part for part in str(pool.get("limit_up_reason") or "").split("+") if part]
        review_bars = entry.get("bars") or [] if entry.get("status") == "ok" else []
        reviews.append(review_stock(
            symbol=symbol, name=name, trade_date=trade_date, quote_day=quote_day, minutes=stock_minutes,
            bars=review_bars, limit=limit, sector=sector,
            sector_series=inputs["boards"].get(key, []) if key else [], signals=inputs["signals"].get(symbol, []),
            themes=themes, tape=tape.get(symbol, []), auction=auctions.get(symbol)))
        reviews[-1]["history"]["as_of"] = entry.get("as_of") or trade_date.isoformat()
    if deps.industry_board_flow is not None and any(r["sector"].get("status") != "ok" for r in reviews):
        try:
            flows = {str(item.get("sector_key")): item for item in await deps.industry_board_flow()}
        except Exception:  # noqa: BLE001 - the review keeps whatever sector evidence it has
            flows = {}
        for review in reviews:
            sector = review["sector"]
            board = flows.get(str(sector.get("sector_key")))
            close_pct = _num(review["day"].get("close_pct"))
            if sector.get("status") == "ok" or not board or close_pct is None or _num(board.get("change_pct")) is None:
                continue
            sector.update({"status": "close_only", "label": sector.get("label") or board.get("label"),
                           "sector_close_pct": round(float(board["change_pct"]), 2), "stock_close_pct": close_pct,
                           "relative_strength_pct": round(close_pct - float(board["change_pct"]), 2),
                           "note": "no intraday board series; relative strength at the close only"})
            review["patterns"] = pattern_labels(review["day"], review["path"], review["limit"], sector, review["history"],
                                                review["tape"])
    summary = {"version": REVIEW_VERSION, "trade_date": trade_date.isoformat(), **summarize(reviews),
               "coverage": {"watched": len(symbols), "with_minutes": sum(1 for r in reviews if r["path"].get("status") == "ok"),
                            "with_sector_series": sum(1 for r in reviews if r["sector"].get("status") == "ok"),
                            "with_tape": sum(1 for r in reviews if r["tape"].get("status") == "ok"),
                            "tape_scans": max((r["tape"].get("scans") or 0 for r in reviews), default=0),
                            "with_auction": sum(1 for r in reviews if r["day"]["auction"].get("status") == "ok"),
                            "minutes_stored": minutes_stored,
                            "board_snapshots": max((len(v) for v in inputs["boards"].values()), default=0)}}
    stored = await deps.run_database(lambda: persist_reviews(deps.database, trade_date, reviews, summary),
                                     timeout_seconds=90) if persist else 0
    return {"status": "completed", "trade_date": trade_date.isoformat(), "reviews": len(reviews), "stored": stored,
            "summary": summary, **({} if persist else {"items": reviews})}
