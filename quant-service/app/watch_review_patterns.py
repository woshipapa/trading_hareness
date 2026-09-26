"""Patterns across the stored watch daily reviews (research only).

The daily review labels each watched stock's day (its shape, limit-up
behaviour, industry relation, opening auction, seal openings...).  Across
days this module answers two questions:

* which labels (and label pairs) precede which next-session outcomes - the
  next open gap, close, high and low against the labelled day's close, and
  the three-session move - compared with the baseline of all reviewed days;
* how each stock relates to its industry and peer group over time - how
  often it co-moves, leads or lags, beats the board, touches or holds the
  limit - summarised as a profile.

Outcomes come from split-adjusted daily bars, so a later dividend or split
does not fake a move.  Nothing here feeds a live rule.
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from itertools import combinations
from statistics import mean, median
from typing import Any, Awaitable, Callable, Mapping
from zoneinfo import ZoneInfo

PATTERN_VERSION = "watch-review-patterns-v1"
OUTCOME_FIELDS = ("next_gap_pct", "next_close_pct", "next_high_pct", "next_low_pct", "close_3d_pct")
_CN = ZoneInfo("Asia/Shanghai")


def _num(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def next_day_outcomes(reviews: list[Mapping[str, Any]],
                      bars_by_symbol: Mapping[str, Mapping[str, Any]]) -> dict[tuple[str, str], dict[str, float]]:
    """(symbol, trade_date) -> the following sessions measured from the reviewed day's close."""
    outcomes: dict[tuple[str, str], dict[str, float]] = {}
    for review in reviews:
        symbol, day = str(review.get("symbol")), str(review.get("trade_date") or "")
        entry = bars_by_symbol.get(symbol) or {}
        bars = entry.get("bars") or [] if entry.get("status") == "ok" else []
        index = next((i for i, bar in enumerate(bars) if bar.get("date") == day.replace("-", "")), None)
        if index is None or index + 1 >= len(bars) or not _num(bars[index].get("close")):
            continue
        base, following = float(bars[index]["close"]), bars[index + 1]
        outcome = {name: round((float(following[field]) / base - 1) * 100, 2)
                   for name, field in (("next_gap_pct", "open"), ("next_close_pct", "close"),
                                       ("next_high_pct", "high"), ("next_low_pct", "low"))}
        if index + 3 < len(bars):
            outcome["close_3d_pct"] = round((float(bars[index + 3]["close"]) / base - 1) * 100, 2)
        outcomes[(symbol, day)] = outcome
    return outcomes


def _outcome_stats(items: list[Mapping[str, float]]) -> dict[str, Any]:
    stats: dict[str, Any] = {"with_outcome": len(items)}
    for field in OUTCOME_FIELDS:
        values = [item[field] for item in items if field in item]
        if values:
            stats[field] = {"mean": round(mean(values), 2), "median": round(median(values), 2)}
    closes = [item["next_close_pct"] for item in items if "next_close_pct" in item]
    if closes:
        stats["next_close_up_rate"] = round(sum(1 for value in closes if value > 0) / len(closes), 3)
    return stats


def pattern_stats(reviews: list[Mapping[str, Any]], outcomes: Mapping[tuple[str, str], Mapping[str, float]],
                  *, min_count: int = 3) -> dict[str, Any]:
    """Each label and label pair: how often, on whom, and what the next sessions did."""
    groups: dict[str, list[Mapping[str, Any]]] = {}
    for review in reviews:
        labels = sorted(set(review.get("patterns") or []))
        for label in labels:
            groups.setdefault(label, []).append(review)
        for left, right in combinations(labels, 2):
            groups.setdefault(f"{left}+{right}", []).append(review)

    def describe(members: list[Mapping[str, Any]]) -> dict[str, Any]:
        found = [outcomes[key] for key in ((str(r["symbol"]), str(r["trade_date"])) for r in members) if key in outcomes]
        return {"count": len(members),
                "examples": [f"{r.get('name')} {r.get('trade_date')}" for r in members[-5:]],
                **_outcome_stats(found)}

    singles = {label: describe(members) for label, members in groups.items() if "+" not in label}
    pairs = {label: describe(members) for label, members in groups.items()
             if "+" in label and len(members) >= min_count}
    order = lambda item: -item[1]["count"]  # noqa: E731
    return {
        "baseline": describe(list(reviews)),
        "labels": dict(sorted(singles.items(), key=order)),
        "label_pairs": dict(sorted(pairs.items(), key=order)),
    }


def stock_profiles(reviews: list[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    """Per stock across days: its labels and its relation to its industry and peer group."""
    by_symbol: dict[str, list[Mapping[str, Any]]] = {}
    for review in reviews:
        by_symbol.setdefault(str(review.get("symbol")), []).append(review)
    profiles: dict[str, dict[str, Any]] = {}
    for symbol, items in sorted(by_symbol.items()):
        sectors = [r.get("sector") or {} for r in items]
        series = [s for s in sectors if s.get("status") == "ok"]
        strengths = [_num(s.get("relative_strength_pct")) for s in sectors if s.get("status") in ("ok", "close_only")]
        strengths = [value for value in strengths if value is not None]
        correlations = [value for value in (_num(s.get("corr_5m")) for s in series) if value is not None]
        leads = Counter(s.get("lead") for s in series if s.get("lead"))
        peers = [(r.get("tape") or {}).get("peer_group") or {} for r in items]
        peer_corr = [value for value in (_num(p.get("corr_5m_stock_vs_breadth")) for p in peers) if value is not None]
        limits = [r.get("limit") or {} for r in items]
        labels = Counter(label for r in items for label in r.get("patterns") or [])
        industry = Counter(s.get("label") for s in sectors if s.get("label"))
        relation: list[str] = []
        if sum(leads.values()) >= 3 and leads.get("stock_leads", 0) / sum(leads.values()) >= 0.5:
            relation.append("常领先板块")
        if len(correlations) >= 3:
            average = mean(correlations)
            relation.append("跟随板块" if average >= 0.6 else "独立于板块" if average < 0.2 else "与板块中度联动")
        if len(strengths) >= 3:
            share = sum(1 for value in strengths if value > 0) / len(strengths)
            if share >= 0.7:
                relation.append("常强于板块")
            elif share <= 0.3:
                relation.append("常弱于板块")
        profiles[symbol] = {
            "name": items[-1].get("name"), "days": len(items),
            "first_day": min(str(r.get("trade_date")) for r in items), "last_day": max(str(r.get("trade_date")) for r in items),
            "industry": industry.most_common(1)[0][0] if industry else None,
            "labels": dict(labels.most_common(10)),
            "sector": {
                "days_with_series": len(series), "days_with_strength": len(strengths),
                "mean_relative_strength_pct": round(mean(strengths), 2) if strengths else None,
                "beat_board_rate": round(sum(1 for value in strengths if value > 0) / len(strengths), 3) if strengths else None,
                "mean_corr_5m": round(mean(correlations), 3) if correlations else None,
                "lead": dict(leads),
            },
            "peer_group": {
                "days": sum(1 for p in peers if p),
                "mean_corr_5m_vs_breadth": round(mean(peer_corr), 3) if peer_corr else None,
                "breadth_peaked_first_days": sum(1 for p in peers if p.get("breadth_peaked_first")),
            },
            "limit": {
                "touched_days": sum(1 for item in limits if item.get("touched")),
                "sealed_close_days": sum(1 for item in limits if item.get("sealed_at_close")),
                "board_opens": sum(int(item.get("board_opens") or 0) for item in limits),
            },
            "relation": relation,
        }
    return profiles


def read_reviews_range(database: Any, start: date, end: date) -> list[dict[str, Any]]:
    """Stored per-stock reviews (latest per stock and day) between two sessions, inclusive."""
    lower = datetime.combine(start, time(15, 0), tzinfo=_CN)
    upper = datetime.combine(end, time(15, 0), tzinfo=_CN)
    with database.transaction() as connection:
        rows = connection.execute(
            """SELECT DISTINCT ON (symbol,effective_at) symbol,effective_at,payload FROM quant.raw_market_observations
                WHERE provider_key='quant_scan' AND capability='watch_daily_review' AND market='cn'
                  AND symbol<>'watch:review' AND effective_at>=%s AND effective_at<=%s
                ORDER BY symbol,effective_at,available_at DESC""", (lower, upper)).fetchall()
    return sorted((dict(row["payload"]) for row in rows), key=lambda r: (str(r.get("trade_date")), str(r.get("symbol"))))


@dataclass(frozen=True)
class WatchPatternDependencies:
    database: Any
    run_database: Callable[..., Awaitable[Any]]
    plan_bars: Callable[..., dict[str, dict[str, Any]]]
    today: Callable[[], date]


async def watch_review_patterns(start: date, end: date, deps: WatchPatternDependencies, *,
                                min_count: int = 3) -> dict[str, Any]:
    reviews = await deps.run_database(lambda: read_reviews_range(deps.database, start, end), timeout_seconds=60)
    symbols = sorted({str(review.get("symbol")) for review in reviews})
    today = deps.today()
    # Calendar days bound sessions from above; +10 covers the three-session outcome.
    limit = min(400, (today - start).days + 10)
    bars = await deps.run_database(lambda: deps.plan_bars(deps.database, symbols, through=today, limit=limit),
                                   timeout_seconds=90) if symbols else {}
    outcomes = next_day_outcomes(reviews, bars)
    days = sorted({str(review.get("trade_date")) for review in reviews})
    return {
        "version": PATTERN_VERSION, "start": start.isoformat(), "end": end.isoformat(),
        "sessions": days, "reviews": len(reviews), "stocks": len(symbols), "with_outcome": len(outcomes),
        "note": "labels are rule-based; next-session outcomes are measured from the labelled day's close "
                "on split-adjusted bars; small counts are anecdotes, not patterns",
        **pattern_stats(reviews, outcomes, min_count=min_count),
        "stock_profiles": stock_profiles(reviews),
    }


def default_window(end: date, sessions_back: int = 60) -> date:
    return end - timedelta(days=int(sessions_back * 1.5))


__all__ = ["PATTERN_VERSION", "WatchPatternDependencies", "default_window", "next_day_outcomes", "pattern_stats",
           "read_reviews_range", "stock_profiles", "watch_review_patterns"]
