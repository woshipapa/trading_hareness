"""Self-computed short-term market indicators from pools and the all-A tape.

``sentiment_cycle`` reads the five settled readings from daily bars.  This
module computes the intraday/close versions a desk reads live -- the numbers
开盘啦-style dashboards show -- from vendor limit pools plus the all-A
Level-1 snapshot:

* limit-up / limit-down / broken counts and the seal rate;
* the board ladder and its promotion rate per layer (1->2, 2->3, 3->4, 4+);
* yesterday's limit-ups today: mean move ("premium") and red rate, overall
  and for yesterday's multi-board names;
* the up/down distribution in buckets, and turnover versus the prior session;
* a transparent concept strength score.

The formulas are published, not fitted; a vendor's proprietary sentiment
score will not match them, and nothing here is a trading signal.
"""

from __future__ import annotations

from dataclasses import dataclass
from statistics import mean, median, pstdev
from typing import Any, Iterable, Mapping, Sequence

from ...market_rules import a_share_limit_ratio, is_st_security_name


INDICATOR_VERSION = "market-sentiment-indicators-v1"
#: A move within this of the board's limit counts as "at limit" in the
#: distribution; pool membership, not this tolerance, drives the counts.
LIMIT_TOLERANCE_PCT = 0.15
DISTRIBUTION_BUCKETS: tuple[tuple[str, float, float], ...] = (
    ("gt_7", 7.0, float("inf")), ("5_to_7", 5.0, 7.0), ("3_to_5", 3.0, 5.0), ("0_to_3", 0.0, 3.0),
    ("m3_to_0", -3.0, 0.0), ("m5_to_m3", -5.0, -3.0), ("m7_to_m5", -7.0, -5.0), ("lt_m7", float("-inf"), -7.0),
)


@dataclass(frozen=True)
class StrengthWeights:
    """Concept strength = weighted z-scores; weights are declared, not fitted."""

    pct_change: float = 0.4
    limit_up_members: float = 0.4
    member_up_ratio: float = 0.2


def limit_ratio_pct(symbol: str, name: str | None = None, trade_date: object = None) -> float:
    return a_share_limit_ratio(symbol, is_st_security_name(name), trade_date) * 100


def _symbol_set(rows: Iterable[Mapping[str, Any]]) -> set[str]:
    return {str(row.get("symbol") or row.get("thscode") or "").upper() for row in rows} - {""}


def _board_count(row: Mapping[str, Any]) -> int:
    for key in ("board_count", "continue_day_cnt", "board_num", "lbc"):
        value = row.get(key)
        try:
            parsed = int(float(value))
        except (TypeError, ValueError):
            continue
        if parsed >= 1:
            return parsed
    return 1


def ladder(limit_up_rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    heights: dict[str, int] = {}
    for row in limit_up_rows:
        symbol = str(row.get("symbol") or row.get("thscode") or "").upper()
        if symbol:
            heights[symbol] = max(heights.get(symbol, 0), _board_count(row))
    distribution: dict[int, int] = {}
    for height in heights.values():
        distribution[height] = distribution.get(height, 0) + 1
    top = max(heights.values()) if heights else 0
    return {
        "max_board_height": top,
        "distribution": {str(height): distribution[height] for height in sorted(distribution)},
        "multi_board_count": sum(count for height, count in distribution.items() if height >= 2),
        "gaps_below_top": [height for height in range(1, top) if height not in distribution],
        "top_symbols": sorted(symbol for symbol, height in heights.items() if height == top)[:10] if top else [],
        "heights": heights,
    }


def promotion_by_layer(previous_heights: Mapping[str, int], today_sealed: set[str]) -> dict[str, Any]:
    """Share of yesterday's n-board names sealed again today, per layer."""
    layers: dict[str, dict[str, Any]] = {}
    for label, low, high in (("1to2", 1, 1), ("2to3", 2, 2), ("3to4", 3, 3), ("4plus", 4, 99)):
        base = [symbol for symbol, height in previous_heights.items() if low <= height <= high]
        promoted = [symbol for symbol in base if symbol in today_sealed]
        layers[label] = {"base": len(base), "promoted": len(promoted),
                         "rate": round(len(promoted) / len(base), 4) if base else None}
    total_base = len(previous_heights)
    total_promoted = sum(1 for symbol in previous_heights if symbol in today_sealed)
    return {"layers": layers, "overall": {"base": total_base, "promoted": total_promoted,
                                          "rate": round(total_promoted / total_base, 4) if total_base else None}}


def prior_limit_performance(previous_heights: Mapping[str, int],
                            snapshot: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    """Today's move of yesterday's limit-ups: premium (mean %) and red rate."""
    def summary(symbols: Sequence[str]) -> dict[str, Any]:
        moves = [float(snapshot[symbol]["pct_change"]) for symbol in symbols
                 if symbol in snapshot and snapshot[symbol].get("pct_change") is not None]
        if not moves:
            return {"count": 0, "observed": 0, "premium_pct": None, "red_rate": None, "median_pct": None}
        return {"count": len(symbols), "observed": len(moves), "premium_pct": round(mean(moves), 4),
                "red_rate": round(sum(move > 0 for move in moves) / len(moves), 4),
                "median_pct": round(median(moves), 4)}
    all_symbols = sorted(previous_heights)
    return {"all": summary(all_symbols),
            "multi_board": summary([symbol for symbol in all_symbols if previous_heights[symbol] >= 2]),
            "first_board": summary([symbol for symbol in all_symbols if previous_heights[symbol] == 1])}


def distribution(snapshot: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    buckets = {label: 0 for label, _low, _high in DISTRIBUTION_BUCKETS}
    at_limit_up = at_limit_down = up = down = flat = 0
    moves = []
    for symbol, row in snapshot.items():
        pct = row.get("pct_change")
        if pct is None:
            continue
        pct = float(pct)
        moves.append(pct)
        limit = limit_ratio_pct(symbol, row.get("name"))
        if pct >= limit - LIMIT_TOLERANCE_PCT:
            at_limit_up += 1
        elif pct <= -limit + LIMIT_TOLERANCE_PCT:
            at_limit_down += 1
        if pct > 0:
            up += 1
        elif pct < 0:
            down += 1
        else:
            flat += 1
        for label, low, high in DISTRIBUTION_BUCKETS:
            if (low <= pct < high) if label != "0_to_3" else (0.0 < pct < 3.0):
                buckets[label] += 1
                break
    return {"observed": len(moves), "up": up, "down": down, "flat": flat,
            "up_ratio": round(up / len(moves), 4) if moves else None,
            "median_pct": round(median(moves), 4) if moves else None,
            "at_limit_up_by_price": at_limit_up, "at_limit_down_by_price": at_limit_down, "buckets": buckets}


def turnover(snapshot: Mapping[str, Mapping[str, Any]], previous_total: float | None) -> dict[str, Any]:
    total = sum(float(row.get("turnover") or 0) for row in snapshot.values())
    change = (total / previous_total - 1) * 100 if previous_total else None
    return {"total_yuan": round(total, 2), "previous_total_yuan": previous_total,
            "change_pct": round(change, 4) if change is not None else None}


def _z(values: Mapping[str, float]) -> dict[str, float]:
    if not values:
        return {}
    spread = pstdev(values.values()) if len(values) > 1 else 0.0
    center = mean(values.values())
    return {key: ((value - center) / spread if spread else 0.0) for key, value in values.items()}


def concept_strength(
    index_quotes: Mapping[str, Mapping[str, Any]],
    membership: Mapping[str, set[str]] | None,
    sealed: set[str],
    snapshot: Mapping[str, Mapping[str, Any]],
    *,
    weights: StrengthWeights = StrengthWeights(),
    limit: int = 20,
    min_members: int = 5,
) -> list[dict[str, Any]]:
    """Rank concepts by index move, sealed members and member breadth."""
    pct: dict[str, float] = {}
    limit_ups: dict[str, float] = {}
    up_ratio: dict[str, float] = {}
    for code, quote in index_quotes.items():
        move = quote.get("price_change_ratio_pct")
        if move is None:
            continue
        members = (membership or {}).get(code)
        if membership is not None and (not members or len(members) < min_members):
            continue
        pct[code] = float(move)
        if members:
            limit_ups[code] = float(len(members & sealed))
            observed = [snapshot[symbol]["pct_change"] for symbol in members
                        if symbol in snapshot and snapshot[symbol].get("pct_change") is not None]
            up_ratio[code] = sum(float(value) > 0 for value in observed) / len(observed) if observed else 0.0
    z_pct, z_limit, z_up = _z(pct), _z(limit_ups), _z(up_ratio)
    scored = []
    for code in pct:
        score = weights.pct_change * z_pct.get(code, 0.0)
        if membership is not None:
            score += weights.limit_up_members * z_limit.get(code, 0.0) + weights.member_up_ratio * z_up.get(code, 0.0)
        scored.append({
            "index_code": code, "index_name": index_quotes[code].get("index_name"),
            "pct_change": round(pct[code], 4), "limit_up_members": int(limit_ups.get(code, 0)) if membership else None,
            "member_up_ratio": round(up_ratio[code], 4) if code in up_ratio else None,
            "score": round(score, 4),
        })
    scored.sort(key=lambda item: item["score"], reverse=True)
    return scored[:limit]


def market_sentiment_snapshot(
    *,
    limit_up_rows: Sequence[Mapping[str, Any]],
    broken_rows: Sequence[Mapping[str, Any]],
    limit_down_rows: Sequence[Mapping[str, Any]],
    previous_limit_up_rows: Sequence[Mapping[str, Any]],
    snapshot_rows: Sequence[Mapping[str, Any]],
    previous_turnover_total: float | None = None,
    index_quotes: Mapping[str, Mapping[str, Any]] | None = None,
    concept_membership: Mapping[str, set[str]] | None = None,
) -> dict[str, Any]:
    """Combine the pools and the tape into one indicator record.

    ``snapshot_rows`` need ``symbol``, ``pct_change`` and ``turnover`` (yuan);
    pool rows need ``symbol``/``thscode`` and, for the ladder, a board count.
    """
    snapshot = {str(row.get("symbol") or "").upper(): row for row in snapshot_rows if row.get("symbol")}
    sealed = _symbol_set(limit_up_rows)
    broken = _symbol_set(broken_rows) - sealed
    downs = _symbol_set(limit_down_rows)
    today_ladder = ladder(limit_up_rows)
    previous_heights = ladder(previous_limit_up_rows)["heights"]
    attempted = len(sealed) + len(broken)
    heights = today_ladder.pop("heights")
    return {
        "methodology": INDICATOR_VERSION,
        "semantics": "self_computed_from_vendor_pools_and_level1_snapshot; not_a_vendor_score; research_only",
        "limit_up_count": len(sealed), "limit_down_count": len(downs), "broken_count": len(broken),
        "seal_rate": round(len(sealed) / attempted, 4) if attempted else None,
        "broken_rate": round(len(broken) / attempted, 4) if attempted else None,
        "ladder": today_ladder,
        "promotion": promotion_by_layer(previous_heights, sealed),
        "prior_limit_up_today": prior_limit_performance(previous_heights, snapshot),
        "distribution": distribution(snapshot),
        "turnover": turnover(snapshot, previous_turnover_total),
        "concept_strength": concept_strength(index_quotes or {}, concept_membership, sealed, snapshot)
        if index_quotes else [],
        "high_board_symbols": sorted(symbol for symbol, height in heights.items() if height >= 3),
    }


__all__ = [
    "DISTRIBUTION_BUCKETS", "INDICATOR_VERSION", "StrengthWeights", "concept_strength", "distribution",
    "ladder", "limit_ratio_pct", "market_sentiment_snapshot", "prior_limit_performance",
    "promotion_by_layer", "turnover",
]
