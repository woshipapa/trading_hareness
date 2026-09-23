"""One definition of "how did this signal do", shared by every strategy.

``xiaojie_outcome_settlement`` worked these out first and its reasoning holds
for any flag this system raises:

* a name flagged while already sealed at the limit **cannot be bought**, so
  its return is not a result - counting it inflates every rate it touches.
  On 2026-08-27, 63 of 111 xiaojie observations were sealed at entry and
  produced 0 gains; averaging them with the 48 unsealed ones halved the
  apparent edge and hid the real defect, which is flagging too late;
* three holding periods answer different questions and disagree sharply, so
  all three are recorded rather than one being chosen as "the" return;
* a session's move is credited against the day's cross-sectional median, or
  a strategy gets credit for the tape it rode;
* returns are reported net of one round trip, because at the size of edge
  these strategies produce the cost is the difference between a marginal
  positive and a clear negative.

The teacher-review outcome review used entry-to-close gross with none of
this and reported 9 hits for 2026-09-22 where 2 were flagged at the limit
price and could not have been bought.  These functions exist so the two
families are measured the same way and cannot drift apart again.

Pure: callers supply the bars.
"""

from __future__ import annotations

from typing import Any, Mapping

from .ashare_reality import round_trip_cost_pct

#: A price within this much of the limit counts as locked there.
SEALED_TOLERANCE = 0.005

#: What an entry that could not be taken is called, wherever it is reported.
UNBUYABLE = "unbuyable"


def _num(value: Any) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def locked_at(price: Any, limit_up: Any, *, tolerance: float = SEALED_TOLERANCE) -> bool | None:
    """True when ``price`` sits at the limit (so no seller is available)."""
    value, limit = _num(price), _num(limit_up)
    if value is None or limit is None or limit <= 0:
        return None
    return value >= limit - tolerance


def entry_evaluable(entry_price: Any, *, limit_up: Any = None, flagged_sealed: Any = None) -> bool:
    """False when the flag fired on a sealed board, whatever raised it.

    ``flagged_sealed`` is the scan's own reading; the price/limit comparison
    is the fallback for an archive that kept only the price.
    """
    if flagged_sealed is not None:
        return not bool(flagged_sealed)
    locked = locked_at(entry_price, limit_up)
    return True if locked is None else not locked


def returns(entry_price: Any, *, session_close: Any = None, next_open: Any = None,
            next_close: Any = None, cost_pct: float | None = None) -> dict[str, Any]:
    """The three holding periods, gross and net of one round trip each.

    ``next_open_to_close_pct`` is the only one an account could actually have
    earned when the entry price was unavailable, so it is never derived from
    the entry price.
    """
    entry = _num(entry_price)
    cost = float(round_trip_cost_pct()) if cost_pct is None else float(cost_pct)

    def pct(target: Any, base: float | None) -> float | None:
        value = _num(target)
        return round((value / base - 1) * 100, 4) if value is not None and base else None

    session = pct(session_close, entry)
    to_next_close = pct(next_close, entry)
    open_to_close = pct(next_close, _num(next_open))
    return {
        "entry_price": entry,
        "session_return_pct": session,
        "entry_to_next_close_pct": to_next_close,
        "next_open_to_close_pct": open_to_close,
        "round_trip_cost_pct": round(cost, 4),
        "net_session_return_pct": None if session is None else round(session - cost, 4),
        "net_next_open_to_close_pct": None if open_to_close is None else round(open_to_close - cost, 4),
    }


def excess(session_return_pct: Any, benchmark_pct: Any) -> float | None:
    """The session move minus the day's cross-sectional median."""
    value, benchmark = _num(session_return_pct), _num(benchmark_pct)
    return None if value is None or benchmark is None else round(value - benchmark, 4)


def measure(entry: Mapping[str, Any] | None, bar: Mapping[str, Any] | None,
            next_bar: Mapping[str, Any] | None = None, *, benchmark_pct: Any = None,
            cost_pct: float | None = None) -> dict[str, Any]:
    """Everything above for one flagged name, in one call.

    ``entry`` carries ``price`` and, when the scan recorded it, ``sealed``;
    ``bar`` is the flagged session and ``next_bar`` the one after it, which
    normally arrives a day later and fills the forward columns on a re-run.
    """
    if not entry or _num(entry.get("price")) is None:
        return {"evaluable": None, "reason": "no_entry"}
    bar = bar or {}
    next_bar = next_bar or {}
    evaluable = entry_evaluable(entry.get("price"), limit_up=bar.get("limit_up_price"),
                                flagged_sealed=entry.get("sealed"))
    measured = returns(entry.get("price"), session_close=bar.get("close"), next_open=next_bar.get("open"),
                       next_close=next_bar.get("close"), cost_pct=cost_pct)
    measured["next_open_locked"] = locked_at(next_bar.get("open"), next_bar.get("limit_up_price"))
    measured["excess_session_pct"] = excess(measured["session_return_pct"], benchmark_pct)
    measured["benchmark_session_pct"] = _num(benchmark_pct)
    measured["evaluable"] = evaluable
    measured["reason"] = None if evaluable else "sealed_at_entry"
    return measured


__all__ = [
    "SEALED_TOLERANCE", "UNBUYABLE", "entry_evaluable", "excess", "locked_at", "measure", "returns",
]
