"""Limit-up, broken-board and limit-down pools derived from the TDX all-A snapshot and the MAC limit prices.

The all-A snapshot (``tdx_public`` ``quote.all_a_snapshot``) gives every A share's price and day high, the MAC
batch quote (``tdx_mac`` ``limits.prices``) its exchange limit prices.  Comparing the two says which securities
sit at, or touched, a limit at the moment of the snapshot, and nothing more: the vendor pools also give the seal
times, the reason, the board count and the seal money.  These pools are research evidence and never replace a
vendor pool for a decision.
"""

from __future__ import annotations

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
                       observed_at: datetime) -> tuple[list[dict[str, Any]], int]:
    """The members of the three pools, each row tagged ``pool``, and how many snapshot securities were left out.

    ``snapshot_rows`` (``symbol``, ``price``, ``high``) and ``limit_rows`` (``symbol``, ``limit_up``,
    ``limit_down``) are joined by symbol and compared in cents:

    * ``limit_up``: the price equals the up limit;
    * ``broken``: the day high equals the up limit and the price is below it;
    * ``limit_down``: the price equals the down limit.

    A security with no limit row, or whose limit row lacks a positive up or down limit, is left out and counted; no
    evidence file holds the limit row of a security without limits, so a limit that is not a price is what counts
    as none.
    A row whose price is not positive is never a member.  A member carries ``symbol``, ``price``, ``high``,
    ``up_limit`` (``down_limit`` in ``limit_down``) in yuan, and ``observed_at``.
    """
    bands = {row["symbol"]: (_cents(row["limit_up"]), _cents(row["limit_down"])) for row in limit_rows}
    members: list[dict[str, Any]] = []
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
            members.append({"pool": "limit_up", **member, "up_limit": up / 100})
        if high == up and price < up:
            members.append({"pool": "broken", **member, "up_limit": up / 100})
        if price == down:
            members.append({"pool": "limit_down", **member, "down_limit": down / 100})
    return members, without_limit


async def fetch_limit_pools(*, trade_date: date) -> CapabilityEvidence:
    """Read the all-A snapshot, then the limit prices of exactly its symbols, and derive the three pools.

    The snapshot is live, so only the current session can be derived.  ``coverage`` is the share of snapshot
    securities that have a limit price; ``available_at`` is the later of the two collection times.
    """
    today = cn_today()
    if trade_date != today:
        raise ValueError(
            f"limit pools are derived from the live all-A snapshot, so only the current session ({today}) can be derived, "
            f"not {trade_date}")
    snapshot = await tdx_legacy_misc.fetch_all_a_snapshot()
    limits = await tdx_mac.fetch_limit_prices(symbols=[row["symbol"] for row in snapshot.rows])
    members, without_limit = derive_limit_pools(snapshot.rows, limits.rows, snapshot.available_at_max)
    warnings = (*snapshot.warnings, *limits.warnings)
    if without_limit:
        warnings += (f"limit_price_missing={without_limit}",)
    available_at = max(snapshot.available_at_max, limits.available_at_max)
    return CapabilityEvidence(
        members, coverage=(len(snapshot.rows) - without_limit) / len(snapshot.rows),
        available_at_min=available_at, available_at_max=available_at, warnings=warnings)


__all__ = ["derive_limit_pools", "fetch_limit_pools"]
