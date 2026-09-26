"""Split settled strategy outcomes by the market state they were chosen in.

``market_regime_daily`` (the index regime) and ``sentiment_cycle_daily`` (the
short-term sentiment stage) are materialized every session, but nothing read
them back against outcomes: a strategy that only works in one regime looked
the same as one that works everywhere.  This reads each candidate ledger
session's mean net return next to the regime and stage of its signal date.

Point in time: a ledger idea is chosen after the close of ``as_of_date`` and
entered the next session, and both readings of ``as_of_date`` are computed
from that close, so they were known when the idea was chosen.  Everything here
is descriptive research: holding windows overlap (ten sessions), so the
t-statistics are optimistic, and a stratum below ``MIN_STRATUM_SESSIONS`` is
reported but flagged as too small to read.
"""

from __future__ import annotations

import math
from collections import defaultdict
from datetime import date
from typing import Any, Iterable, Mapping

from .t1_settlement import SETTLEMENT_VERSION

MIN_STRATUM_SESSIONS = 20
STRATUM_KINDS = ("regime", "sentiment_stage")
UNKNOWN_STRATUM = "unknown"

#: One row per (strategy, signal session): the session's mean net return and
#: the two readings of that session.  A missing reading is its own stratum.
STRATIFIED_SESSION_RETURNS_SQL = """
SELECT o.strategy_key AS variant_key, o.as_of_date AS period_date,
       avg(o.net_return) AS period_return,
       coalesce(regime.regime_label, 'unknown') AS regime,
       coalesce(sentiment.stage, 'unknown') AS sentiment_stage
  FROM quant.strategy_daily_candidate_outcomes o
  LEFT JOIN quant.market_regime_daily regime ON regime.trading_date=o.as_of_date
  LEFT JOIN quant.sentiment_cycle_daily sentiment ON sentiment.trading_date=o.as_of_date
 WHERE o.settlement_version=%s AND o.net_return IS NOT NULL AND o.exit_date<=%s
   AND (%s::text IS NULL OR o.strategy_key=%s)
 GROUP BY o.strategy_key, o.as_of_date, regime.regime_label, sentiment.stage
 ORDER BY o.strategy_key, o.as_of_date
"""


def stratified_session_returns_parameters(as_of_date: date, strategy_key: str | None) -> tuple[Any, ...]:
    return (SETTLEMENT_VERSION, as_of_date, strategy_key, strategy_key)


def _summary(returns: list[float]) -> dict[str, Any]:
    count = len(returns)
    mean = sum(returns) / count
    if count > 1:
        variance = sum((value - mean) ** 2 for value in returns) / (count - 1)
        stdev = math.sqrt(variance)
    else:
        stdev = None
    t_stat = mean / (stdev / math.sqrt(count)) if stdev else None
    return {
        "sessions": count,
        "mean_net_return": mean,
        "stdev": stdev,
        "t_stat": t_stat,
        "hit_rate": sum(1 for value in returns if value > 0) / count,
        "sample_sufficient": count >= MIN_STRATUM_SESSIONS,
    }


def stratify_session_returns(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Per strategy: its overall line, then each regime and sentiment stratum.

    ``mean_minus_all`` is the stratum's mean less the strategy's overall mean:
    the part of the line that the market state explains, if any.
    """
    overall: dict[str, list[float]] = defaultdict(list)
    strata: dict[tuple[str, str, str], list[float]] = defaultdict(list)
    for row in rows:
        if row.get("period_return") is None:
            continue
        variant = str(row["variant_key"])
        value = float(row["period_return"])
        overall[variant].append(value)
        for kind in STRATUM_KINDS:
            strata[(variant, kind, str(row.get(kind) or UNKNOWN_STRATUM))].append(value)
    result = []
    for variant in sorted(overall):
        baseline = _summary(overall[variant])
        entries = []
        for (key, kind, stratum), values in sorted(strata.items()):
            if key != variant:
                continue
            summary = _summary(values)
            entries.append({"kind": kind, "stratum": stratum, **summary,
                            "mean_minus_all": summary["mean_net_return"] - baseline["mean_net_return"]})
        result.append({"strategy_key": variant, "all": baseline, "strata": entries})
    return result


def stratification_payload(rows: Iterable[Mapping[str, Any]], as_of_date: date) -> dict[str, Any]:
    return {
        "as_of_date": str(as_of_date),
        "settlement_version": SETTLEMENT_VERSION,
        "return_basis": "session_mean_net_return_by_signal_date",
        "strata_kinds": list(STRATUM_KINDS),
        "min_stratum_sessions": MIN_STRATUM_SESSIONS,
        "strategies": stratify_session_returns(rows),
        "caveats": [
            "holding windows overlap, so t_stat is optimistic",
            "descriptive only; a stratum difference is not a promoted rule",
        ],
        "research_only": True,
        "live_effect": "none",
    }


__all__ = [
    "MIN_STRATUM_SESSIONS", "STRATIFIED_SESSION_RETURNS_SQL", "STRATUM_KINDS", "UNKNOWN_STRATUM",
    "stratification_payload", "stratified_session_returns_parameters", "stratify_session_returns",
]
