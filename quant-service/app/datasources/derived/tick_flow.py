"""Level-1 tick (分笔) flow metrics: active buy/sell and size buckets.

Public ticks are Level-1 aggregated prints with a buy/sell/neutral side, not
Level-2 order-by-order data.  "Large order" here therefore means a *print*
whose amount crosses a threshold -- an estimate, labelled as such.

Sessions are split by the exchange clock:

* ``A`` prints before 09:25 are the zero-volume call-auction indicative
  matches (TDX side code 8): the auction curve, never flow;
* the 09:25 print(s) are the opening call-auction match;
* 09:30-14:57 is continuous trading; 14:57-15:00 the closing call auction;
* ``P`` prints after 15:00 are after-hours fixed-price trades, kept apart.

Pure functions only; adapters for Tencent and TDX produce :class:`Tick`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence


#: Print-amount buckets in yuan: (label, lower bound inclusive), largest first.
DEFAULT_AMOUNT_BUCKETS: tuple[tuple[str, float], ...] = (
    ("super_large", 1_000_000.0), ("large", 200_000.0), ("medium", 40_000.0), ("small", 0.0),
)
LARGE_BUCKET_FLOOR = 200_000.0
OPENING_AUCTION = "09:25"
CLOSE_AUCTION_START = "14:57"
SESSION_CLOSE = "15:00"
TICK_FLOW_VERSION = "tick-flow-v1"


@dataclass(frozen=True)
class Tick:
    time: str          # HH:MM or HH:MM:SS, exchange local time
    price: float
    volume_shares: float
    amount: float      # yuan; price * volume when the source omits it
    side: str          # B active buy, S active sell, N neutral, A auction indicative, P after-hours


def _bucket(amount: float, buckets: Sequence[tuple[str, float]]) -> str:
    for label, lower in buckets:
        if amount >= lower:
            return label
    return buckets[-1][0]


def _empty_side() -> dict[str, float]:
    return {"buy_amount": 0.0, "sell_amount": 0.0, "neutral_amount": 0.0,
            "buy_volume": 0.0, "sell_volume": 0.0, "neutral_volume": 0.0, "prints": 0}


def _add(target: dict[str, float], tick: Tick) -> None:
    key = {"B": "buy", "S": "sell"}.get(tick.side, "neutral")
    target[f"{key}_amount"] += tick.amount
    target[f"{key}_volume"] += tick.volume_shares
    target["prints"] += 1


def _finish(side: dict[str, float]) -> dict[str, float | None]:
    total = side["buy_amount"] + side["sell_amount"] + side["neutral_amount"]
    directional = side["buy_amount"] + side["sell_amount"]
    return {
        **{key: round(value, 2) for key, value in side.items()},
        "net_active_amount": round(side["buy_amount"] - side["sell_amount"], 2),
        "active_buy_ratio": round(side["buy_amount"] / directional, 4) if directional else None,
        "total_amount": round(total, 2),
    }


def _phase(tick: Tick) -> str:
    minute = tick.time[:5]
    if tick.side == "A":
        return "auction_indicative"
    if tick.side == "P" or minute > SESSION_CLOSE:
        return "after_hours"
    if minute <= OPENING_AUCTION:
        return "opening_auction"
    if minute >= CLOSE_AUCTION_START:
        return "closing_auction"
    return "continuous"


def summarize_ticks(
    ticks: Iterable[Tick],
    *,
    buckets: Sequence[tuple[str, float]] = DEFAULT_AMOUNT_BUCKETS,
    window_minutes: int = 30,
) -> dict[str, Any]:
    """Daily active-flow summary, per size bucket and per time window."""
    phases: dict[str, list[Tick]] = {}
    for tick in sorted(ticks, key=lambda item: item.time):
        phases.setdefault(_phase(tick), []).append(tick)
    traded = phases.get("continuous", []) + phases.get("closing_auction", [])
    overall = _empty_side()
    by_bucket = {label: _empty_side() for label, _ in buckets}
    by_window: dict[str, dict[str, float]] = {}
    for tick in traded:
        _add(overall, tick)
        _add(by_bucket[_bucket(tick.amount, buckets)], tick)
        minutes = int(tick.time[:2]) * 60 + int(tick.time[3:5])
        start = minutes - minutes % window_minutes
        _add(by_window.setdefault(f"{start // 60:02d}:{start % 60:02d}", _empty_side()), tick)

    def _auction(rows: list[Tick]) -> dict[str, Any] | None:
        executed = [tick for tick in rows if tick.volume_shares > 0]
        if not executed:
            return None
        return {"price": executed[-1].price, "volume_shares": sum(tick.volume_shares for tick in executed),
                "amount": round(sum(tick.amount for tick in executed), 2), "prints": len(executed)}

    indicative = phases.get("auction_indicative", [])
    curve = None
    if indicative:
        prices = [tick.price for tick in indicative if tick.price > 0]
        curve = {"points": len(indicative), "first_price": indicative[0].price, "last_price": indicative[-1].price,
                 "min_price": min(prices) if prices else None, "max_price": max(prices) if prices else None,
                 "last_before_0920": next((tick.price for tick in reversed(indicative) if tick.time[:5] < "09:20"), None)}
    large_labels = {label for label, lower in buckets if lower >= LARGE_BUCKET_FLOOR}
    large_net = sum(by_bucket[label]["buy_amount"] - by_bucket[label]["sell_amount"] for label in large_labels)
    largest = sorted(traded, key=lambda tick: tick.amount, reverse=True)[:10]
    return {
        "methodology": TICK_FLOW_VERSION,
        "semantics": "level1_prints_side_classified; large_order_is_print_amount_estimate_not_level2",
        "prints": sum(len(rows) for rows in phases.values()),
        "auction_curve": curve,
        "opening_auction": _auction(phases.get("opening_auction", [])),
        "closing_auction": _auction(phases.get("closing_auction", [])),
        "after_hours": _auction(phases.get("after_hours", [])),
        "overall": _finish(overall),
        "by_bucket": {label: _finish(side) for label, side in by_bucket.items()},
        "large_net_active_amount": round(large_net, 2),
        "by_window": {label: _finish(side) for label, side in sorted(by_window.items())},
        "largest_prints": [{"time": tick.time, "price": tick.price, "amount": round(tick.amount, 2), "side": tick.side}
                           for tick in largest],
        "bucket_bounds_yuan": {label: lower for label, lower in buckets},
    }


def parse_tencent_detail(text: str) -> list[Tick]:
    """Parse one ``stock.gtimg.cn`` detail page.

    Records are ``idx/HH:MM:SS/price/change/volume_lots/amount_yuan/B|S|M``.
    """
    start, end = text.find('"'), text.rfind('"')
    if start < 0 or end <= start:
        return []
    ticks = []
    for record in text[start + 1:end].split("|"):
        parts = record.split("/")
        if len(parts) < 7:
            continue
        try:
            price, lots, amount = float(parts[2]), float(parts[4]), float(parts[5])
        except ValueError:
            continue
        side = {"B": "B", "S": "S"}.get(parts[6].strip().upper(), "N")
        ticks.append(Tick(parts[1].strip(), price, lots * 100, amount, side))
    return ticks


def ticks_from_tdx(rows: Iterable[Mapping[str, Any]]) -> list[Tick]:
    """TDX ticks carry lots and no amount; amount is price x shares."""
    result = []
    for row in rows:
        try:
            price, shares = float(row["price"]), float(row["volume_lots"]) * 100
        except (KeyError, TypeError, ValueError):
            continue
        result.append(Tick(str(row["time"]), price, shares, price * shares, str(row.get("side") or "N")))
    return result


__all__ = [
    "CLOSE_AUCTION_START", "DEFAULT_AMOUNT_BUCKETS", "OPENING_AUCTION", "TICK_FLOW_VERSION", "Tick",
    "parse_tencent_detail", "summarize_ticks", "ticks_from_tdx",
]
