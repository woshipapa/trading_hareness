"""Research-only rules for the opening-auction desk."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from types import MappingProxyType
from typing import Any, Mapping, Sequence
from zoneinfo import ZoneInfo

from .strategy_outcome_measures import SEALED_TOLERANCE

VERSION = "auction-desk-v1"
SHANGHAI = ZoneInfo("Asia/Shanghai")
AUCTION_POOL_MAX_SYMBOLS = 200
PRE_CLOSE_ABSOLUTE_TOLERANCE = 0.011
PRE_CLOSE_RELATIVE_TOLERANCE = 0.001

BUCKET_STATISTICS: Mapping[str, Mapping[str, Any]] = MappingProxyType({
    "below_-5%": MappingProxyType({
        "label": "gap below -5%: on average higher at the close",
        "open_to_close_mean": 0.0183,
        "share_open_to_close_le_minus_5": 0.079,
        "open_to_next_open_mean": -0.0022,
        "n": 277,
    }),
    "-5..-2%": MappingProxyType({
        "label": "gap from -5% to -2%: on average higher at the close",
        "open_to_close_mean": 0.0097,
        "share_open_to_close_le_minus_5": 0.141,
        "open_to_next_open_mean": -0.0021,
        "n": 1146,
    }),
    "-2..0%": MappingProxyType({
        "label": "gap from -2% to 0%: on average higher at the close",
        "open_to_close_mean": 0.0003,
        "share_open_to_close_le_minus_5": 0.133,
        "open_to_next_open_mean": -0.0062,
        "n": 2424,
    }),
    "0..2%": MappingProxyType({
        "label": "gap from 0% to 2%: on average higher at the close",
        "open_to_close_mean": 0.0021,
        "share_open_to_close_le_minus_5": 0.126,
        "open_to_next_open_mean": -0.0017,
        "n": 3680,
    }),
    "2..5%": MappingProxyType({
        "label": "gap from 2% to 5%: on average lower at the close",
        "open_to_close_mean": -0.0011,
        "share_open_to_close_le_minus_5": 0.172,
        "open_to_next_open_mean": -0.0032,
        "n": 2557,
    }),
    "5..9%": MappingProxyType({
        "label": "gap from 5% to 9%: on average lower at the close",
        "open_to_close_mean": -0.0080,
        "share_open_to_close_le_minus_5": 0.247,
        "open_to_next_open_mean": -0.0046,
        "n": 1176,
    }),
    "9%+_not_at_limit": MappingProxyType({
        "label": "gap of 9% or more without opening at the limit: on average lower at the close",
        "open_to_close_mean": -0.0202,
        "share_open_to_close_le_minus_5": 0.271,
        "open_to_next_open_mean": -0.0068,
        "n": 299,
    }),
    "opens_at_limit": MappingProxyType({
        "label": "opens at the limit: on average lower at the close and higher at the next open",
        "open_to_close_mean": -0.0178,
        "share_open_to_close_le_minus_5": 0.155,
        "open_to_next_open_mean": 0.0238,
        "n": 754,
    }),
})


def auction_timetable(session_date: date) -> tuple[datetime, ...]:
    """Return every scheduled pool tick for one Shanghai session date."""
    start = datetime.combine(session_date, time(9, 15, 5), tzinfo=SHANGHAI)
    regular = [start + timedelta(seconds=10 * index) for index in range(59)]
    extras = (
        datetime.combine(session_date, time(9, 19, 50), tzinfo=SHANGHAI),
        datetime.combine(session_date, time(9, 20, 5), tzinfo=SHANGHAI),
        datetime.combine(session_date, time(9, 24, 55), tzinfo=SHANGHAI),
    )
    final_start = datetime.combine(session_date, time(9, 25, 5), tzinfo=SHANGHAI)
    final = [final_start + timedelta(seconds=5 * index) for index in range(12)]
    return tuple(sorted(set(regular) | set(extras) | set(final)))


def next_auction_tick(after: datetime) -> datetime | None:
    """Return the first scheduled tick strictly after ``after``."""
    day = after.astimezone(SHANGHAI).date()
    for tick in auction_timetable(day):
        if tick > after:
            return tick
    return None


def build_auction_pool(
    scored_symbols: Sequence[str], broken_board_symbols: Sequence[str], watchlist_symbols: Sequence[str],
    *, cap: int = AUCTION_POOL_MAX_SYMBOLS,
) -> list[str]:
    """Deduplicate the three sources in collection order and apply the cap."""
    return list(dict.fromkeys((*scored_symbols, *broken_board_symbols, *watchlist_symbols)))[:cap]


def open_gap(open_price: float, previous_close: float) -> float:
    return open_price / previous_close - 1


def opens_at_limit(open_price: float, limit_up_price: float, *, tolerance: float = SEALED_TOLERANCE) -> bool:
    return open_price >= limit_up_price - tolerance


def gap_bucket(gap: float, *, opened_at_limit: bool = False) -> str:
    if opened_at_limit:
        return "opens_at_limit"
    if gap < -0.05:
        return "below_-5%"
    if gap < -0.02:
        return "-5..-2%"
    if gap < 0:
        return "-2..0%"
    if gap < 0.02:
        return "0..2%"
    if gap < 0.05:
        return "2..5%"
    if gap < 0.09:
        return "5..9%"
    return "9%+_not_at_limit"


def accepts_snapshot_row(row: Mapping[str, Any], previous_close: float) -> bool:
    """Accept only today's final snapshot matched to yesterday's close."""
    pre_close = float(row["pre_close_price"])
    return (
        row["data_status"] == "final"
        and abs(pre_close - previous_close) <= max(
            PRE_CLOSE_ABSOLUTE_TOLERANCE, previous_close * PRE_CLOSE_RELATIVE_TOLERANCE,
        )
    )


__all__ = [
    "AUCTION_POOL_MAX_SYMBOLS", "BUCKET_STATISTICS", "PRE_CLOSE_ABSOLUTE_TOLERANCE",
    "PRE_CLOSE_RELATIVE_TOLERANCE", "VERSION", "accepts_snapshot_row", "auction_timetable",
    "build_auction_pool", "gap_bucket", "open_gap", "next_auction_tick", "opens_at_limit",
]
