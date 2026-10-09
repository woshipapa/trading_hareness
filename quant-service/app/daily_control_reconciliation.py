"""Same-date daily-control coverage reconciliation.

The provider synchronizer writes complete responses atomically, but a
successful older run can still leave one projection (usually ``daily_basic``)
short of the canonical bar cross-section.  This module keeps the repair
decision separate from provider transport: read the persisted coverage, fetch
through the existing capability resolver only when needed, then read it back
before allowing research stages to continue.
"""

from __future__ import annotations

import math
from collections.abc import Awaitable, Callable, Mapping
from datetime import date
from typing import Any

from .adjustment_factor_semantics import persisted_factor_semantics_sql

EQUITY_SYMBOL_PATTERN = (
    r"^(?:(?:60[0135]|68[0-9])[0-9]{3}\.SH|"
    r"(?:000|001|002|003|300|301|302)[0-9]{3}\.SZ|[489][0-9]{5}\.BJ)$"
)
MINIMUM_BAR_COVERAGE_RATIO = 0.95

# This is a capability plan, not a claim that every source is selected on
# every run.  The reconciliation receipt replaces the selected entries with
# the provider returned by the resolver, while retaining the owner source for
# canonical bars and factors when those projections are already persisted.
DEFAULT_SOURCE_PLAN: dict[str, dict[str, str]] = {
    "daily_bars": {
        "primary": "longhuvip_composite",
        "fallback": "none_retired",
        "store": "quant.canonical_bars_daily",
    },
    "daily_basic": {
        "primary": "owner_persisted_multi_source",
        "fallback": "none_retired",
        "store": "quant.daily_fundamentals",
        "semantics": "per-symbol provenance; valuation-only records do not supply turnover or capital fields",
    },
    "adjustment_factor": {
        "primary": "longhu_qfq_derived",
        "fallback": "owner_persisted_historical_checkpoints",
        "store": "quant.daily_adjustment_factors",
    },
    "trade_limits": {
        "primary": "owner_persisted_trade_limits",
        "fallback": "none_retired",
        "store": "quant.daily_trade_limits",
    },
}


DAILY_CONTROL_COVERAGE_SQL = f"""WITH expected AS (
       SELECT count(DISTINCT membership.symbol)::int AS expected_symbols
         FROM quant.universe_membership_history membership
        WHERE membership.universe_key='all_a'
          AND membership.effective_from<=%s
          AND (membership.effective_to IS NULL OR membership.effective_to>%s)
   ), bars AS MATERIALIZED (
       SELECT DISTINCT bar.symbol
         FROM quant.canonical_bars_daily bar
        WHERE bar.trading_date=%s
          AND bar.quality_status='fresh'
          AND bar.available_at < ((bar.trading_date+1)::timestamp AT TIME ZONE 'Asia/Shanghai')
          AND bar.symbol ~ '{EQUITY_SYMBOL_PATTERN}'
   ), fundamentals AS (
       SELECT DISTINCT basic.symbol
         FROM quant.daily_fundamentals basic
         JOIN bars ON bars.symbol=basic.symbol
        WHERE basic.trading_date=%s
          AND basic.available_at < ((basic.trading_date+1)::timestamp AT TIME ZONE 'Asia/Shanghai')
   ), factors AS (
       SELECT DISTINCT factor.symbol
         FROM quant.daily_adjustment_factors factor
         JOIN bars ON bars.symbol=factor.symbol
        WHERE factor.trading_date=%s
          AND factor.adj_factor>0
          AND {persisted_factor_semantics_sql('factor')}
          AND factor.available_at < ((factor.trading_date+1)::timestamp AT TIME ZONE 'Asia/Shanghai')
   ), limits AS (
       SELECT DISTINCT limits.symbol
         FROM quant.daily_trade_limits limits
         JOIN bars ON bars.symbol=limits.symbol
        WHERE limits.trading_date=%s
          AND limits.available_at < ((limits.trading_date+1)::timestamp AT TIME ZONE 'Asia/Shanghai')
   )
   SELECT %s::date AS trading_date,
          expected.expected_symbols,
          (SELECT count(*)::int FROM bars) AS bar_symbols,
          (SELECT count(*)::int FROM fundamentals) AS fundamental_symbols,
          (SELECT count(*)::int FROM factors) AS adjustment_symbols,
          (SELECT count(*)::int FROM limits) AS limit_symbols
     FROM expected"""


def _count(value: Any) -> int:
    return int(value or 0)


def normalize_coverage(row: Mapping[str, Any] | None, trade_date: date) -> dict[str, Any]:
    """Normalize one database aggregate into a secret-free coverage receipt."""
    row = row or {}
    expected = _count(row.get("expected_symbols"))
    bars = _count(row.get("bar_symbols"))
    minimum = math.ceil(expected * MINIMUM_BAR_COVERAGE_RATIO) if expected else 0
    fundamentals = _count(row.get("fundamental_symbols"))
    adjustments = _count(row.get("adjustment_symbols"))
    limits = _count(row.get("limit_symbols"))
    missing = {
        "daily_bars": max(minimum - bars, 0),
        "daily_basic": max(bars - fundamentals, 0),
        "adjustment_factor": max(bars - adjustments, 0),
        "trade_limits": max(bars - limits, 0),
    }
    return {
        "trade_date": trade_date.isoformat(),
        "expected_symbols": expected,
        "minimum_bar_symbols": minimum,
        "bar_symbols": bars,
        "equity_symbols": bars,
        "fundamental_symbols": fundamentals,
        "adjustment_symbols": adjustments,
        "limit_symbols": limits,
        "missing": missing,
        "complete": (
            bars > 0
            and bars >= minimum
            and fundamentals >= bars
            and adjustments >= bars
            and limits >= bars
        ),
    }


def needs_reconciliation(coverage: Mapping[str, Any]) -> bool:
    """Return whether a same-date provider repair is required."""
    return not bool(coverage.get("complete"))


def read_coverage(connection: Any, trade_date: date) -> dict[str, Any]:
    """Read persisted coverage through a caller-owned transaction."""
    row = connection.execute(
        DAILY_CONTROL_COVERAGE_SQL,
        (trade_date, trade_date, trade_date, trade_date, trade_date, trade_date, trade_date),
    ).fetchone()
    return normalize_coverage(row, trade_date)


async def reconcile(
    trade_date: date,
    *,
    read: Callable[[date], Awaitable[Mapping[str, Any]]],
    sync: Callable[[date], Awaitable[Mapping[str, Any]]],
) -> dict[str, Any]:
    """Repair missing same-date controls and verify the persisted result."""
    before = dict(await read(trade_date))
    receipt: dict[str, Any] = {
        "trade_date": trade_date.isoformat(),
        "source_plan": DEFAULT_SOURCE_PLAN,
        "coverage_before": before,
    }
    if not needs_reconciliation(before):
        receipt.update({"status": "unchanged", "coverage_after": before, "provider_sync": None})
        return receipt

    provider_sync = dict(await sync(trade_date))
    after = dict(await read(trade_date))
    receipt.update({
        "status": "completed" if not needs_reconciliation(after) else "blocked",
        "coverage_after": after,
        "provider_sync": provider_sync,
    })
    if needs_reconciliation(after):
        receipt["reason"] = "same-date daily control coverage remains incomplete after capability-routed synchronization"
    return receipt


__all__ = [
    "DAILY_CONTROL_COVERAGE_SQL",
    "DEFAULT_SOURCE_PLAN",
    "EQUITY_SYMBOL_PATTERN",
    "MINIMUM_BAR_COVERAGE_RATIO",
    "needs_reconciliation",
    "normalize_coverage",
    "read_coverage",
    "reconcile",
]
