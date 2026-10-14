"""Research-only rules for the opening-auction desk."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from types import MappingProxyType
from typing import Any, Mapping, Sequence
from zoneinfo import ZoneInfo

from .limit_up_risk import _limit_up
from .strategy_outcome_measures import SEALED_TOLERANCE

VERSION = "auction-desk-v1"
SHANGHAI = ZoneInfo("Asia/Shanghai")
AUCTION_POOL_MAX_SYMBOLS = 200
PRE_CLOSE_ABSOLUTE_TOLERANCE = 0.011
PRE_CLOSE_RELATIVE_TOLERANCE = 0.001


def optional_float(value: Any) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None

BUCKET_STATISTICS: Mapping[str, Mapping[str, Any]] = MappingProxyType({
    "below_-5%": MappingProxyType({
        "label": "gap below -5%: on average higher at the close",
        "open_to_close_mean": 0.0183,
        "open_to_close_se": 0.0043,
        "share_open_to_close_le_minus_5": 0.079,
        "open_to_next_open_mean": -0.0022,
        "open_to_next_open_se": 0.0048,
        "n": 277,
    }),
    "-5..-2%": MappingProxyType({
        "label": "gap from -5% to -2%: on average higher at the close",
        "open_to_close_mean": 0.0097,
        "open_to_close_se": 0.0024,
        "share_open_to_close_le_minus_5": 0.141,
        "open_to_next_open_mean": -0.0021,
        "open_to_next_open_se": 0.0030,
        "n": 1146,
    }),
    "-2..0%": MappingProxyType({
        "label": "gap from -2% to 0%: no clear drift to the close",
        "open_to_close_mean": 0.0003,
        "open_to_close_se": 0.0017,
        "share_open_to_close_le_minus_5": 0.133,
        "open_to_next_open_mean": -0.0062,
        "open_to_next_open_se": 0.0019,
        "n": 2424,
    }),
    "0..2%": MappingProxyType({
        "label": "gap from 0% to 2%: no clear drift to the close",
        "open_to_close_mean": 0.0021,
        "open_to_close_se": 0.0014,
        "share_open_to_close_le_minus_5": 0.126,
        "open_to_next_open_mean": -0.0017,
        "open_to_next_open_se": 0.0018,
        "n": 3680,
    }),
    "2..5%": MappingProxyType({
        "label": "gap from 2% to 5%: no clear drift to the close; large intraday drops were more frequent than in the flat buckets (17.2% against 12.6-13.3%)",
        "open_to_close_mean": -0.0011,
        "open_to_close_se": 0.0016,
        "share_open_to_close_le_minus_5": 0.172,
        "open_to_next_open_mean": -0.0032,
        "open_to_next_open_se": 0.0024,
        "n": 2557,
    }),
    "5..9%": MappingProxyType({
        "label": "gap from 5% to 9%: on average lower at the close",
        "open_to_close_mean": -0.0080,
        "open_to_close_se": 0.0026,
        "share_open_to_close_le_minus_5": 0.247,
        "open_to_next_open_mean": -0.0046,
        "open_to_next_open_se": 0.0039,
        "n": 1176,
    }),
    "9%+_not_at_limit": MappingProxyType({
        "label": "gap of 9% or more without opening at the limit: on average lower at the close",
        "open_to_close_mean": -0.0202,
        "open_to_close_se": 0.0037,
        "share_open_to_close_le_minus_5": 0.271,
        "open_to_next_open_mean": -0.0068,
        "open_to_next_open_se": 0.0063,
        "n": 299,
    }),
    "opens_at_limit": MappingProxyType({
        "label": "opens at the limit: on average lower at the close and higher at the next open",
        "open_to_close_mean": -0.0178,
        "open_to_close_se": 0.0021,
        "share_open_to_close_le_minus_5": 0.155,
        "open_to_next_open_mean": 0.0238,
        "open_to_next_open_se": 0.0044,
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
    pre_close = optional_float(row.get("pre_close_price"))
    return (
        str(row.get("data_status") or "") == "final"
        and pre_close is not None
        and abs(pre_close - previous_close) <= max(
            PRE_CLOSE_ABSOLUTE_TOLERANCE, previous_close * PRE_CLOSE_RELATIVE_TOLERANCE,
        )
    )


def build_auction_table(
    scored_rows: Sequence[Mapping[str, Any]], snapshot_rows: Sequence[Mapping[str, Any]],
    *, trade_date: date | None = None,
) -> dict[str, Any]:
    """Build the research-only 09:25 table from one normalized tick."""
    by_symbol = {str(row.get("thscode") or row.get("ts_code") or row.get("symbol")): row for row in snapshot_rows}
    rows: list[dict[str, Any]] = []
    for scored in scored_rows:
        symbol = str(scored["symbol"])
        previous_close = optional_float(scored.get("previous_close"))
        snapshot = by_symbol.get(symbol)
        if previous_close is None or snapshot is None or not accepts_snapshot_row(snapshot, previous_close):
            continue
        opening = optional_float(snapshot.get("open_price"))
        if opening is None or opening <= 0:
            opening = optional_float(snapshot.get("auction_price"))
        if opening is None or opening <= 0:
            continue
        gap = open_gap(opening, previous_close)
        limit_price = _limit_up(symbol.split(".", 1)[0], {"pre_close": previous_close})
        at_limit = opens_at_limit(opening, limit_price)
        bucket = gap_bucket(gap, opened_at_limit=at_limit)
        rows.append({
            "symbol": symbol, "name": scored.get("name"), "open": opening, "gap": gap,
            "opens_at_limit": at_limit, "bucket": bucket,
            "risk_rank": scored.get("risk_rank"), "boards": scored.get("boards"),
            "sealed_from_open": scored.get("sealed_from_open"),
            "amount": snapshot.get("auction_amount"), "volume": snapshot.get("auction_volume"),
            "unmatched": snapshot.get("auction_unmatched"),
        })
    return {
        "trade_date": trade_date.isoformat() if trade_date else None,
        "version": VERSION, "rows": rows,
        "bucket_statistics": {key: dict(value) for key, value in BUCKET_STATISTICS.items()},
        "coverage": {"scored": len(scored_rows), "accepted": len(rows), "missing": len(scored_rows) - len(rows)},
        "research_only": True, "live_effect": "none",
    }


#: At 09:25 only the open is known, so the table's bucket stays ``opens_at_limit``.  Once the day has traded the
#: track tells two things apart that a buyer's ledger must not mix: a board that stayed one-word (the low is at the
#: limit too) could not be bought all day, one that opened at the limit and reopened could be bought at the limit
#: price (a conservative backtest still does not assume it).
OPENS_AT_LIMIT_ONE_WORD = "opens_at_limit:one_word"
OPENS_AT_LIMIT_REOPENED = "opens_at_limit:reopened"
FILL_RULES: Mapping[str, str] = MappingProxyType({
    OPENS_AT_LIMIT_ONE_WORD: "not buyable all day: counted as cash (0 return) and kept in the sample, not dropped",
    OPENS_AT_LIMIT_REOPENED: "buyable at the limit price once the board opens; a conservative rule does not assume the fill",
})


def realised_bucket(row: Mapping[str, Any], bar: Mapping[str, Any] | None) -> str:
    """The bucket of a row once the day's bar is known; ``opens_at_limit`` is split, every other bucket is kept."""
    bucket = str(row["bucket"])
    if bucket != "opens_at_limit" or bar is None:
        return bucket
    opening, low = optional_float(row.get("open")), optional_float(bar.get("low"))
    if opening is None or low is None:
        return bucket
    return OPENS_AT_LIMIT_ONE_WORD if low >= opening - SEALED_TOLERANCE else OPENS_AT_LIMIT_REOPENED


def auction_track_summary(readings: Sequence[Mapping[str, Any]], bars: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Summarize realised open-to-close returns by session and auction bucket."""
    by_pair = {(str(row["symbol"]), str(row["trading_date"])): row for row in bars}
    sessions = []
    for reading in readings:
        day = str(reading["trade_date"])
        buckets: dict[str, list[float]] = {}
        for row in reading.get("rows") or []:
            bar = by_pair.get((str(row["symbol"]), day))
            opening, close = optional_float(row.get("open")), optional_float(bar.get("close")) if bar else None
            if opening is not None and close is not None and opening > 0:
                buckets.setdefault(realised_bucket(row, bar), []).append(close / opening - 1)
        sessions.append({"trade_date": day, "buckets": {
            bucket: {"stocks": len(values), "open_to_close_mean": sum(values) / len(values),
                     "share_open_to_close_le_minus_5": sum(value <= -0.05 for value in values) / len(values),
                     **({"fill_rule": FILL_RULES[bucket]} if bucket in FILL_RULES else {})}
            for bucket, values in buckets.items()
        }})
    return {"version": VERSION, "days": sessions, "research_only": True, "live_effect": "none"}


__all__ = [
    "AUCTION_POOL_MAX_SYMBOLS", "BUCKET_STATISTICS", "PRE_CLOSE_ABSOLUTE_TOLERANCE",
    "PRE_CLOSE_RELATIVE_TOLERANCE", "VERSION", "accepts_snapshot_row", "auction_timetable",
    "build_auction_pool", "gap_bucket", "open_gap", "next_auction_tick", "opens_at_limit", "optional_float",
    "auction_track_summary", "build_auction_table", "realised_bucket",
    "OPENS_AT_LIMIT_ONE_WORD", "OPENS_AT_LIMIT_REOPENED", "FILL_RULES",
]
