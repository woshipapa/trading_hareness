"""One T+1-aware settlement rule for every daily-horizon research outcome.

Three copies of the same settlement SQL each priced a daily idea as "buy the
next open, sell the close ``horizon - 1`` sessions later".  That rule had four
problems this module replaces:

* ``horizon_days=1`` sold at the close of the day it bought - a round trip
  T+1 settlement forbids.  The earliest exit here is the close of the session
  after entry, and the settled horizon records what was actually held.
* An exit session that closed sealed at limit-down, or was suspended, was
  valued at its close as if the position could be sold there.  The exit rolls
  forward to the next session that could actually sell, up to a bound.
* Returns used raw prices, so an ex-rights date inside the window read as a
  loss.  Prices are adjusted by ``adj_factor`` when every bar has one.
* Returns were gross, and the benchmark was CSI 300 - the wrong yardstick for
  small-cap short-term ideas.  Net return subtracts the shared round-trip cost
  model, and the benchmark is the equal-weighted A-share market over the same
  sessions (supplied by the caller).

Everything here is pure: bars and the benchmark are inputs, so research,
replay and tests share one implementation.  ``direction=-1`` scores a bearish
thesis symmetrically; A-share accounts cannot generally short, so it is an
evidence score, not a tradable P&L.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, Callable, Mapping, Sequence

from .ashare_reality import round_trip_cost_pct
from .market_rules import a_share_limit_ratio

SETTLEMENT_VERSION = "t1-settlement-v1"
#: The session after entry is the earliest one a T+1 position can be sold in.
MIN_HOLDING_SESSIONS = 2
#: How far an exit blocked by limit-down or suspension may roll before the
#: outcome is valued at the last blocked close and flagged as such.
MAX_EXIT_ROLL_SESSIONS = 10
BENCHMARK_KEY = "all_a_equal_weight"
ROUND_TRIP_COST = round_trip_cost_pct() / Decimal("100")
_LIMIT_TOLERANCE = Decimal("0.001")


def _decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        parsed = Decimal(str(value))
    except (ArithmeticError, ValueError):
        return None
    return parsed if parsed.is_finite() else None


@dataclass(frozen=True)
class SettlementBar:
    trading_date: date
    open: Decimal | None
    high: Decimal | None
    low: Decimal | None
    close: Decimal | None
    pre_close: Decimal | None = None
    adj_factor: Decimal | None = None
    is_suspended: bool = False
    limit_up: Decimal | None = None
    limit_down: Decimal | None = None

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> "SettlementBar":
        return cls(
            trading_date=row["trading_date"], open=_decimal(row.get("open")), high=_decimal(row.get("high")),
            low=_decimal(row.get("low")), close=_decimal(row.get("close")), pre_close=_decimal(row.get("pre_close")),
            adj_factor=_decimal(row.get("adj_factor")), is_suspended=bool(row.get("is_suspended")),
            limit_up=_decimal(row.get("limit_up")), limit_down=_decimal(row.get("limit_down")),
        )


def _limits(bar: SettlementBar, symbol: str) -> tuple[Decimal | None, Decimal | None]:
    """Exact limits when stored, else the board band around the pre-close."""
    if bar.limit_up is not None and bar.limit_down is not None:
        return bar.limit_up, bar.limit_down
    if bar.pre_close is None or bar.pre_close <= 0:
        return bar.limit_up, bar.limit_down
    ratio = Decimal(str(a_share_limit_ratio(symbol, False, bar.trading_date)))
    band_up = (bar.pre_close * (1 + ratio)).quantize(Decimal("0.01"))
    band_down = (bar.pre_close * (1 - ratio)).quantize(Decimal("0.01"))
    return bar.limit_up if bar.limit_up is not None else band_up, bar.limit_down if bar.limit_down is not None else band_down


def entry_block_reason(bar: SettlementBar, direction: int, symbol: str) -> str | None:
    """Why the entry session's open could not be traded, if it could not."""
    if bar.is_suspended:
        return "entry_suspended"
    if bar.open is None or bar.open <= 0:
        return "entry_open_missing"
    limit_up, limit_down = _limits(bar, symbol)
    if direction > 0 and limit_up is not None and bar.open >= limit_up * (1 - _LIMIT_TOLERANCE):
        return "entry_opened_at_limit_up"
    if direction < 0 and limit_down is not None and bar.open <= limit_down * (1 + _LIMIT_TOLERANCE):
        return "entry_opened_at_limit_down"
    return None


def exit_blocked(bar: SettlementBar, direction: int, symbol: str) -> bool:
    """A close sealed against the exit side, or no session, cannot be sold into."""
    if bar.is_suspended or bar.close is None or bar.close <= 0:
        return True
    limit_up, limit_down = _limits(bar, symbol)
    if direction > 0:
        return limit_down is not None and bar.close <= limit_down * (1 + _LIMIT_TOLERANCE)
    return limit_up is not None and bar.close >= limit_up * (1 - _LIMIT_TOLERANCE)


def settle(
    bars: Sequence[SettlementBar | Mapping[str, Any]],
    *,
    symbol: str,
    direction: int,
    horizon_sessions: int,
    benchmark_return: Callable[[date, date], Decimal | None] | None = None,
    round_trip_cost: Decimal = ROUND_TRIP_COST,
    max_exit_roll_sessions: int = MAX_EXIT_ROLL_SESSIONS,
) -> dict[str, Any]:
    """Settle one idea from the sessions after its signal date.

    ``bars`` start at the first session after the signal (the entry session)
    and run in date order.  The result's ``status`` is ``settled``,
    ``pending`` (the window is not fully observable yet) or
    ``entry_unfillable`` (the entry open could not be traded; never settled).
    """
    direction = 1 if int(direction) > 0 else -1
    path = [bar if isinstance(bar, SettlementBar) else SettlementBar.from_row(bar) for bar in bars]
    if not path:
        return {"status": "pending", "reason": "no_entry_session_yet"}
    entry = path[0]
    blocked = entry_block_reason(entry, direction, symbol)
    if blocked:
        return {"status": "entry_unfillable", "reason": blocked, "entry_date": entry.trading_date}

    held_sessions = max(int(horizon_sessions), MIN_HOLDING_SESSIONS)
    target_index = held_sessions - 1
    exit_index = target_index
    while True:
        if exit_index >= len(path):
            return {"status": "pending", "reason": "exit_session_not_observed", "entry_date": entry.trading_date}
        if not exit_blocked(path[exit_index], direction, symbol):
            tradability = "observed_open" if exit_index == target_index else "exit_rolled_past_blocked_session"
            break
        if exit_index - target_index >= max_exit_roll_sessions:
            # Still sealed or suspended after the bound: value it at this close
            # and say so, rather than hold the outcome open indefinitely.
            if path[exit_index].close is None:
                return {"status": "pending", "reason": "exit_blocked_without_price", "entry_date": entry.trading_date}
            tradability = "exit_blocked_valued_at_close"
            break
        exit_index += 1

    window = path[: exit_index + 1]
    exit_bar = window[-1]
    adjusted = all(bar.adj_factor is not None and bar.adj_factor > 0 for bar in window)

    def price(bar: SettlementBar, value: Decimal | None) -> Decimal | None:
        if value is None:
            return None
        return value * bar.adj_factor if adjusted else value

    entry_price = price(entry, entry.open)
    exit_price = price(exit_bar, exit_bar.close)
    highs = [price(bar, bar.high if bar.high is not None else bar.close) for bar in window]
    lows = [price(bar, bar.low if bar.low is not None else bar.close) for bar in window]
    highs = [value for value in highs if value is not None]
    lows = [value for value in lows if value is not None]
    gross = (exit_price / entry_price - 1) * direction
    if direction > 0:
        favorable, adverse = max(highs) / entry_price - 1, min(lows) / entry_price - 1
    else:
        favorable, adverse = entry_price / min(lows) - 1, entry_price / max(highs) - 1
    market = benchmark_return(entry.trading_date, exit_bar.trading_date) if benchmark_return else None
    benchmark = market * direction if market is not None else None
    return {
        "status": "settled",
        "settlement_version": SETTLEMENT_VERSION,
        "direction": direction,
        "entry_date": entry.trading_date,
        "exit_date": exit_bar.trading_date,
        "entry_price": entry.open,
        "exit_price": exit_bar.close,
        "price_basis": "adj_factor_adjusted" if adjusted else "unadjusted_missing_adj_factor",
        "requested_horizon_sessions": int(horizon_sessions),
        "sessions_held": exit_index + 1,
        "exit_rolled_sessions": exit_index - target_index,
        "gross_return": gross,
        "net_return": gross - round_trip_cost,
        "round_trip_cost": round_trip_cost,
        "benchmark_key": BENCHMARK_KEY if benchmark is not None else None,
        "benchmark_return": benchmark,
        "excess_return": gross - benchmark if benchmark is not None else None,
        "maximum_favorable_excursion": favorable,
        "maximum_adverse_excursion": adverse,
        "tradability": tradability,
    }


def market_window_return(
    daily: Mapping[date, Mapping[str, Decimal | None]], entry_date: date, exit_date: date,
) -> Decimal | None:
    """Equal-weight market return from the entry open to the exit close.

    ``daily`` maps each market session to its cross-sectional mean
    ``open_to_close`` and ``close_to_close`` returns.  The entry session
    contributes open-to-close (the idea bought the open), every later session
    up to the exit contributes close-to-close.  A missing entry value makes
    the benchmark unavailable rather than silently shorter.
    """
    first = daily.get(entry_date) or {}
    if first.get("open_to_close") is None:
        return None
    compounded = Decimal(1) + first["open_to_close"]
    for session in sorted(day for day in daily if entry_date < day <= exit_date):
        step = daily[session].get("close_to_close")
        if step is None:
            return None
        compounded *= Decimal(1) + step
    return compounded - 1


__all__ = [
    "BENCHMARK_KEY", "MAX_EXIT_ROLL_SESSIONS", "MIN_HOLDING_SESSIONS", "ROUND_TRIP_COST", "SETTLEMENT_VERSION",
    "SettlementBar", "entry_block_reason", "exit_blocked", "market_window_return", "settle",
]
