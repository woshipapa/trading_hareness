"""Limit-up, broken-board and limit-down pools derived from the TDX all-A snapshot and the MAC limit prices.

The all-A snapshot (``tdx_public`` ``quote.all_a_snapshot``) gives every A share's price and day high, the MAC
batch quote (``tdx_mac`` ``limits.prices``) its exchange limit prices.  Comparing the two says which securities
sit at, or touched, a limit at the moment of the snapshot, and nothing more: the vendor pools also give the seal
times, the reason, the board count and the seal money.  These pools are research evidence and never replace a
vendor pool for a decision.
"""

from __future__ import annotations

from collections import Counter
from datetime import date, datetime
from typing import Any, Iterable, Mapping

from ...market_rules import cn_today
from ..contracts import CapabilityEvidence
from ..sources import tdx_legacy_misc, tdx_mac


def _cents(price: float) -> int:
    """``price`` in exchange ticks of 0.01 yuan.

    The snapshot divides whole cents by 100 and the MAC limit bits are float32 (12.96 arrives as
    12.960000038146973), so two equal prices are not always equal floats; both are whole cents up to a noise far
    below half a cent.
    """
    return round(price * 100)


def derive_limit_pools(snapshot_rows: Iterable[Mapping[str, Any]], limit_rows: Iterable[Mapping[str, Any]],
                       observed_at: datetime) -> tuple[dict[str, list[dict[str, Any]]], int]:
    """The members of the three pools, keyed ``limit_up``, ``broken`` and ``limit_down``, and how many snapshot
    securities were left out.

    ``snapshot_rows`` (``symbol``, ``price``, ``high``) and ``limit_rows`` (``symbol``, ``limit_up``,
    ``limit_down``) are joined by symbol and compared in cents:

    * ``limit_up``: the price equals the up limit;
    * ``broken``: the day high equals the up limit and the price is below it;
    * ``limit_down``: the price equals the down limit.

    A security with no limit row, or whose limit row lacks a positive up or down limit, is left out and counted.  A
    security without a price limit (a new listing, a Beijing first day) answers 0.0 for both limits
    (scripts/data/tdx_mac_limits_all_a_2026-10-10_mac.json).  A row whose price is not positive is never a member.
    A member carries ``symbol``, ``price``, ``high``, ``up_limit`` (``down_limit`` in ``limit_down``) in yuan, and
    ``observed_at``.
    """
    bands = {row["symbol"]: (_cents(row["limit_up"]), _cents(row["limit_down"])) for row in limit_rows}
    pools: dict[str, list[dict[str, Any]]] = {"limit_up": [], "broken": [], "limit_down": []}
    without_limit = 0
    for row in snapshot_rows:
        up, down = bands.get(row["symbol"], (0, 0))
        if up <= 0 or down <= 0:
            without_limit += 1
            continue
        price, high = _cents(row["price"]), _cents(row["high"])
        if price <= 0:
            continue
        member = {"symbol": row["symbol"], "price": price / 100, "high": high / 100, "observed_at": observed_at}
        if price == up:
            pools["limit_up"].append({**member, "up_limit": up / 100})
        if high == up and price < up:
            pools["broken"].append({**member, "up_limit": up / 100})
        if price == down:
            pools["limit_down"].append({**member, "down_limit": down / 100})
    return pools, without_limit


async def _read_limit_pools(*, trade_date: date) -> dict[str, CapabilityEvidence]:
    """Read the all-A snapshot, then the limit prices of exactly its symbols, once, and derive the three pools.

    One evidence per pool, keyed like ``derive_limit_pools`` and sharing the coverage, the warnings and
    ``available_at``: ``coverage`` is the share of snapshot securities that have a limit price, ``available_at``
    the later of the two collection times.  The three adapters return one each; a collector that stores all
    three pools calls this once instead.

    The snapshot is live, so only the current session can be derived.  ``trade_date`` must be the Asia/Shanghai
    date, else nothing is read; and every limit row must carry that date (bit 0x13, the session the host's prices
    belong to), else the call raises.  On a day without a session the rows are dated the last session, so a call
    for that day's date fails.
    """
    today = cn_today()
    if trade_date != today:
        raise ValueError(
            f"limit pools are derived from the live all-A snapshot, so only the current session ({today}) can be derived, "
            f"not {trade_date}")
    snapshot = await tdx_legacy_misc.fetch_all_a_snapshot()
    limits = await tdx_mac.fetch_limit_prices(symbols=[row["symbol"] for row in snapshot.rows])
    stale = Counter(row["trade_date"] for row in limits.rows if row["trade_date"] != trade_date)
    if stale:
        found = ", ".join(f"{day} ({count})" for day, count in sorted(stale.items()))
        raise ValueError(f"{sum(stale.values())} of {len(limits.rows)} limit rows are not dated {trade_date}: {found}")
    pools, without_limit = derive_limit_pools(snapshot.rows, limits.rows, snapshot.available_at_max)
    warnings = (*snapshot.warnings, *limits.warnings)
    if without_limit:
        warnings += (f"limit_price_missing={without_limit}",)
    available_at = max(snapshot.available_at_max, limits.available_at_max)
    coverage = (len(snapshot.rows) - without_limit) / len(snapshot.rows)
    return {pool: CapabilityEvidence(members, coverage=coverage, available_at_min=available_at,
                                     available_at_max=available_at, warnings=warnings)
            for pool, members in pools.items()}


async def fetch_limit_up_pool(*, trade_date: date) -> CapabilityEvidence:
    """The securities whose price is at the up limit at the snapshot moment."""
    return (await _read_limit_pools(trade_date=trade_date))["limit_up"]


async def fetch_broken_pool(*, trade_date: date) -> CapabilityEvidence:
    """The securities whose day high touched the up limit and whose price is below it."""
    return (await _read_limit_pools(trade_date=trade_date))["broken"]


async def fetch_limit_down_pool(*, trade_date: date) -> CapabilityEvidence:
    """The securities whose price is at the down limit at the snapshot moment."""
    return (await _read_limit_pools(trade_date=trade_date))["limit_down"]


__all__ = ["derive_limit_pools", "fetch_broken_pool", "fetch_limit_down_pool", "fetch_limit_up_pool"]
