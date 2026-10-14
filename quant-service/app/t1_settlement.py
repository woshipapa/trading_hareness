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


@dataclass(frozen=True)
class PricedBar:
    """One bar scaled onto the window's common price basis."""

    source: SettlementBar
    trading_date: date
    open: Decimal | None
    high: Decimal | None
    low: Decimal | None
    close: Decimal | None
    pre_close: Decimal | None
    adj_factor: Decimal | None
    is_suspended: bool
    limit_up: Decimal | None
    limit_down: Decimal | None


@dataclass(frozen=True)
class PriceWindow:
    bars: tuple[PricedBar, ...]
    basis: str


def _limits(bar: SettlementBar | PricedBar, symbol: str) -> tuple[Decimal | None, Decimal | None]:
    """Exact limits when stored, else the board band around the pre-close."""
    if bar.limit_up is not None and bar.limit_down is not None:
        return bar.limit_up, bar.limit_down
    if bar.pre_close is None or bar.pre_close <= 0:
        return bar.limit_up, bar.limit_down
    ratio = Decimal(str(a_share_limit_ratio(symbol, False, bar.trading_date)))
    band_up = (bar.pre_close * (1 + ratio)).quantize(Decimal("0.01"))
    band_down = (bar.pre_close * (1 - ratio)).quantize(Decimal("0.01"))
    return bar.limit_up if bar.limit_up is not None else band_up, bar.limit_down if bar.limit_down is not None else band_down


def price_window(bars: Sequence[SettlementBar | Mapping[str, Any]]) -> PriceWindow:
    """Scale every OHLC and limit price using one basis for the supplied window."""
    path = [bar if isinstance(bar, SettlementBar) else SettlementBar.from_row(bar) for bar in bars]
    adjusted = all(bar.adj_factor is not None and bar.adj_factor > 0 for bar in path)
    basis = "adj_factor_adjusted" if adjusted else "unadjusted_missing_adj_factor"

    def scale(value: Decimal | None, bar: SettlementBar) -> Decimal | None:
        return value * bar.adj_factor if adjusted and value is not None else value

    return PriceWindow(tuple(
        PricedBar(
            source=bar, trading_date=bar.trading_date, open=scale(bar.open, bar), high=scale(bar.high, bar),
            low=scale(bar.low, bar), close=scale(bar.close, bar), pre_close=scale(bar.pre_close, bar),
            adj_factor=Decimal("1") if adjusted else None, is_suspended=bar.is_suspended,
            limit_up=scale(bar.limit_up, bar), limit_down=scale(bar.limit_down, bar),
        ) for bar in path
    ), basis)


def entry_block_reason(bar: SettlementBar | PricedBar, direction: int, symbol: str) -> str | None:
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


def sale_blocked(symbol: str, bar: SettlementBar | PricedBar, field: str) -> bool:
    """Whether a long sale at ``field`` is unavailable on this bar."""
    if field not in {"open", "close"}:
        raise ValueError(f"unsupported sale field: {field}")
    price = getattr(bar, field)
    if bar.is_suspended or price is None or price <= 0:
        return True
    _limit_up, limit_down = _limits(bar, symbol)
    return limit_down is not None and price <= limit_down * (1 + _LIMIT_TOLERANCE)


def exit_blocked(bar: SettlementBar | PricedBar, direction: int, symbol: str) -> bool:
    """A close sealed against the exit side, or no session, cannot be sold into."""
    if direction > 0:
        return sale_blocked(symbol, bar, "close")
    if bar.is_suspended or bar.close is None or bar.close <= 0:
        return True
    limit_up, _limit_down = _limits(bar, symbol)
    return limit_up is not None and bar.close >= limit_up * (1 - _LIMIT_TOLERANCE)


def opens_at_up_limit(symbol: str, bar: SettlementBar | PricedBar) -> bool | None:
    if bar.open is None:
        return None
    limit_up, _limit_down = _limits(bar, symbol)
    return limit_up is not None and bar.open >= limit_up * (1 - _LIMIT_TOLERANCE)


def closes_at_up_limit(symbol: str, bar: SettlementBar | PricedBar) -> bool | None:
    if bar.close is None:
        return None
    limit_up, _limit_down = _limits(bar, symbol)
    return limit_up is not None and bar.close >= limit_up * (1 - _LIMIT_TOLERANCE)


ExitDecision = str | tuple[str, Decimal]
ExitRule = Callable[[int, PricedBar], ExitDecision | None]


def walk_exit(
    bars: Sequence[SettlementBar | Mapping[str, Any]] | PriceWindow, *, symbol: str, rule: ExitRule,
    max_exit_roll_sessions: int = MAX_EXIT_ROLL_SESSIONS, direction: int = 1,
) -> dict[str, Any]:
    """Apply a sell rule and roll a blocked sale through later close prices."""
    window = bars if isinstance(bars, PriceWindow) else price_window(bars)
    for index, bar in enumerate(window.bars):
        decision = rule(index, bar)
        if decision is None:
            continue
        if isinstance(decision, tuple):
            field, fixed_price = decision
            if field in {"target", "stop"}:
                return {"status": "settled", "exit_index": index, "field": field,
                        "price": fixed_price, "reason": "rule"}
        else:
            field = decision
        if field not in {"open", "close"}:
            raise ValueError(f"unsupported sale field: {field}")
        for roll in range(max_exit_roll_sessions + 1):
            candidate_index = index + roll
            if candidate_index >= len(window.bars):
                return {"status": "pending", "reason": "not_resolved"}
            candidate = window.bars[candidate_index]
            candidate_field = field if roll == 0 else "close"
            blocked = sale_blocked(symbol, candidate, candidate_field) if direction > 0 else exit_blocked(candidate, -1, symbol)
            if not blocked:
                reason = "rule" if roll == 0 else "exit_rolled_past_blocked_session"
                if field == "open" and roll == 0:
                    reason = "rule"
                return {
                    "status": "settled", "exit_index": candidate_index, "field": candidate_field,
                    "price": getattr(candidate, candidate_field), "reason": reason,
                }
            close_blocked = sale_blocked(symbol, candidate, "close") if direction > 0 else exit_blocked(candidate, -1, symbol)
            if candidate_field == "open" and not close_blocked:
                return {
                    "status": "settled", "exit_index": candidate_index, "field": "close",
                    "price": candidate.close, "reason": "open_blocked_close_fallback",
                }
            if roll == max_exit_roll_sessions:
                if candidate.close is None:
                    return {"status": "pending", "reason": "exit_blocked_without_price"}
                return {
                    "status": "settled", "exit_index": candidate_index, "field": "close",
                    "price": candidate.close, "reason": "exit_blocked_valued_at_close",
                }
    return {"status": "pending", "reason": "not_resolved"}


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
    if target_index >= len(path):
        return {"status": "pending", "reason": "exit_session_not_observed", "entry_date": entry.trading_date}
    walked = walk_exit(path[target_index:], symbol=symbol, direction=direction, rule=lambda _index, _bar: "close",
                       max_exit_roll_sessions=max_exit_roll_sessions)
    if walked["status"] != "settled":
        return {"status": "pending", "reason": "exit_blocked_without_price" if walked["reason"] == "exit_blocked_without_price" else "exit_session_not_observed", "entry_date": entry.trading_date}

    exit_index = target_index + walked["exit_index"]
    window = path[: exit_index + 1]
    priced = price_window(window)
    priced_window = priced.bars
    exit_bar = window[-1]
    priced_entry = priced_window[0]
    priced_exit = priced_window[-1]
    entry_price = priced_entry.open
    exit_price = priced_exit.close
    highs = [bar.high if bar.high is not None else bar.close for bar in priced_window]
    lows = [bar.low if bar.low is not None else bar.close for bar in priced_window]
    highs = [value for value in highs if value is not None]
    lows = [value for value in lows if value is not None]
    gross = (exit_price / entry_price - 1) * direction
    if direction > 0:
        favorable, adverse = max(highs) / entry_price - 1, min(lows) / entry_price - 1
    else:
        favorable, adverse = entry_price / min(lows) - 1, entry_price / max(highs) - 1
    market = benchmark_return(entry.trading_date, exit_bar.trading_date) if benchmark_return else None
    benchmark = market * direction if market is not None else None
    tradability = "observed_open" if walked["exit_index"] == 0 and walked["reason"] == "rule" else walked["reason"]
    return {
        "status": "settled",
        "settlement_version": SETTLEMENT_VERSION,
        "direction": direction,
        "entry_date": entry.trading_date,
        "exit_date": exit_bar.trading_date,
        "entry_price": entry.open,
        "exit_price": exit_bar.close,
        "price_basis": priced.basis,
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
    "PriceWindow", "PricedBar", "SettlementBar", "closes_at_up_limit", "entry_block_reason", "exit_blocked",
    "market_window_return", "opens_at_up_limit", "price_window", "sale_blocked", "settle", "walk_exit",
]
