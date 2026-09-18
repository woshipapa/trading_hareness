"""Turn a configured strategy's own decision into simulated buys and sells.

This decides *what* the paper ledger trades. Whether an order can be filled
stays where it already is - T+1 sellable quantity, limit-up and limit-down
tradability, cost and slippage belong to the paper execution service, and
answering any of that twice would give the simulation two answers to one
question.

Sizing and exits come from the strategy that produced the candidate, not from
here.  xiaojie_leader_flow carries ``position.target_fraction``, its own
staged-entry split and a ``stop_loss`` band chosen per mode; overriding those
with a house default would mean the ledger tests a strategy nobody configured.
A source with no sizing of its own is given the declared fallback and says so
in ``sizing_source``, so a later review can tell which number came from where.

What is decided here is only what the strategy cannot know: how its intent
fits an account that already holds things. Position count, cash and one-entry-
per-name are portfolio bounds, not forecasts.

Simulation only. No broker client exists anywhere on this path.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Iterable, Mapping

#: Used only when a source carries no sizing of its own.
FALLBACK_WEIGHT = 0.10

#: Above this the ledger stops testing the strategy and starts testing
#: diversification.
MAX_OPEN_POSITIONS = 8

#: One pass opens at most this many, so a single loud minute cannot fill the
#: book in one go.
MAX_NEW_PER_PASS = 2

#: A-share board lot.
LOT_SIZE = 100

#: Exit actions a strategy may ask for, and how much of the holding each closes.
EXIT_FRACTIONS = {"exit": 1.0, "reduce_or_exit": 1.0, "reduce_half": 0.5}


def _number(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed == parsed else None


def strategy_intent(candidate: Mapping[str, Any]) -> dict[str, Any]:
    """Normalise one strategy's candidate into a tradable intent.

    Reads the strategy's own contract where it has one.  ``staged_entry`` is
    honoured: a strategy that wants to build in two steps must not have its
    full target opened on the first signal.
    """
    position = candidate.get("position") if isinstance(candidate.get("position"), Mapping) else {}
    stop_loss = candidate.get("stop_loss") if isinstance(candidate.get("stop_loss"), Mapping) else {}
    exit_spec = candidate.get("exit") if isinstance(candidate.get("exit"), Mapping) else {}
    target = _number(position.get("target_fraction"))
    initial = _number(position.get("initial_fraction"))
    entry_fraction = initial if initial is not None and initial > 0 else target
    return {
        "symbol": str(candidate.get("symbol") or ""),
        "strategy_key": str(candidate.get("strategy_key") or candidate.get("source") or "unknown"),
        "mode": candidate.get("mode"),
        "decision": str(candidate.get("decision") or ""),
        "entry_fraction": entry_fraction if entry_fraction is not None else FALLBACK_WEIGHT,
        "sizing_source": "strategy" if entry_fraction is not None else "fallback_weight",
        "target_fraction": target,
        "stop_loss_pct": _number(stop_loss.get("min_pct")),
        "exit_action": str(exit_spec.get("action") or ""),
        "exit_codes": list(exit_spec.get("codes") or []),
        "risk_flags": list(candidate.get("risk_flags") or []),
        "confirmed": bool((candidate.get("large_order") or {}).get("confirmed", True)),
        "direction": str(candidate.get("direction") or "inflow"),
    }


def position_size(equity: Decimal | float, price: Decimal | float, weight: float) -> int:
    """Whole board lots for one name, or zero when a lot does not fit."""
    unit = float(price) * LOT_SIZE
    if unit <= 0 or weight <= 0:
        return 0
    return int((float(equity) * weight) // unit) * LOT_SIZE


def exit_quantity(position: Mapping[str, Any], action: str) -> int:
    """How much of a holding the strategy's exit action closes, in board lots."""
    sellable = int(_number(position.get("sellable_quantity")) or 0)
    fraction = EXIT_FRACTIONS.get(action, 0.0)
    if sellable <= 0 or fraction <= 0:
        return 0
    return max(LOT_SIZE, int(sellable * fraction) // LOT_SIZE * LOT_SIZE) if sellable >= LOT_SIZE else 0


def stop_loss_hit(position: Mapping[str, Any], price: float, stop_loss_pct: float | None) -> bool:
    """Has the strategy's own stop been reached against the simulated cost?"""
    cost = _number(position.get("average_cost"))
    if cost is None or cost <= 0 or stop_loss_pct is None:
        return False
    return (price - cost) / cost * 100 <= -abs(stop_loss_pct)


def plan_paper_orders(
    candidates: Iterable[Mapping[str, Any]],
    positions: Mapping[str, Mapping[str, Any]],
    quotes: Mapping[str, Mapping[str, Any]],
    *,
    equity: Decimal | float,
    cash: Decimal | float,
    max_open_positions: int = MAX_OPEN_POSITIONS,
    max_new_per_pass: int = MAX_NEW_PER_PASS,
) -> dict[str, Any]:
    """Decide this pass's simulated orders from the strategies' own decisions.

    Sells are planned first: freeing a slot before entries are counted lets a
    stopped-out name be replaced in the same pass rather than the next.
    """
    intents = [strategy_intent(candidate) for candidate in candidates]
    by_symbol = {intent["symbol"]: intent for intent in intents if intent["symbol"]}

    sells: list[dict[str, Any]] = []
    held = dict(positions)
    for symbol, position in sorted(positions.items()):
        price = _number((quotes.get(symbol) or {}).get("price"))
        if price is None:
            continue
        intent = by_symbol.get(symbol, {})
        action = intent.get("exit_action") or ""
        reason = None
        if action in EXIT_FRACTIONS:
            reason = f"strategy_exit:{action}"
        elif stop_loss_hit(position, price, intent.get("stop_loss_pct")):
            reason, action = "strategy_stop_loss", "exit"
        if reason is None:
            continue
        quantity = exit_quantity(position, action)
        if quantity <= 0:
            continue
        sells.append({
            "symbol": symbol, "side": "sell", "quantity": quantity, "price": price,
            "reason": reason, "strategy_key": intent.get("strategy_key"),
            "exit_codes": intent.get("exit_codes") or [],
            "average_cost": _number(position.get("average_cost")),
        })
        if quantity >= int(_number(position.get("quantity")) or 0):
            held.pop(symbol, None)

    open_slots = max(0, max_open_positions - len(held))
    buys: list[dict[str, Any]] = []
    remaining_cash = float(cash)
    skipped: list[dict[str, Any]] = []
    for intent in intents:
        symbol = intent["symbol"]
        if not symbol:
            continue
        reason = None
        if intent["exit_action"] in EXIT_FRACTIONS:
            # The same strategy is asking to close this name. Opening it in the
            # same pass would have the ledger act on both halves of one
            # contradictory instruction.
            reason = f"strategy_is_exiting:{intent['exit_action']}"
        elif intent["decision"] and intent["decision"] != "research_candidate":
            reason = f"strategy_decision:{intent['decision']}"
        elif intent["direction"] != "inflow":
            reason = "not_an_inflow_candidate"
        elif not intent["confirmed"]:
            reason = "large_order_unconfirmed"
        elif symbol in held:
            # A hot board or a persistent signal re-qualifies the same name
            # every pass; averaging up on that is the cadence talking.
            reason = "already_held"
        elif len(buys) >= min(max_new_per_pass, open_slots):
            reason = "position_budget_exhausted"
        if reason:
            skipped.append({"symbol": symbol, "reason": reason})
            continue
        price = _number((quotes.get(symbol) or {}).get("price"))
        if price is None or price <= 0:
            skipped.append({"symbol": symbol, "reason": "no_usable_price"})
            continue
        quantity = position_size(equity, price, intent["entry_fraction"])
        if quantity <= 0:
            skipped.append({"symbol": symbol, "reason": "one_lot_exceeds_the_entry_fraction"})
            continue
        notional = price * quantity
        if notional > remaining_cash:
            skipped.append({"symbol": symbol, "reason": "insufficient_paper_cash"})
            continue
        remaining_cash -= notional
        buys.append({
            "symbol": symbol, "side": "buy", "quantity": quantity, "price": price,
            "notional": round(notional, 2), "strategy_key": intent["strategy_key"],
            "mode": intent["mode"], "entry_fraction": intent["entry_fraction"],
            "sizing_source": intent["sizing_source"], "target_fraction": intent["target_fraction"],
            "stop_loss_pct": intent["stop_loss_pct"], "risk_flags": intent["risk_flags"],
        })
    return {
        "sells": sells, "buys": buys, "skipped": skipped,
        "open_positions": len(held), "open_slots": open_slots,
        "cash_after_plan": round(remaining_cash, 2),
        "boundary": "paper simulation only; no broker client on this path",
    }


__all__ = [
    "EXIT_FRACTIONS", "FALLBACK_WEIGHT", "LOT_SIZE", "MAX_NEW_PER_PASS", "MAX_OPEN_POSITIONS",
    "exit_quantity", "plan_paper_orders", "position_size", "stop_loss_hit", "strategy_intent",
]
