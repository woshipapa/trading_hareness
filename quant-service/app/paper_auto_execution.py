"""Turn confirmed research candidates into simulated buys and sells.

This decides *what* to trade in the paper ledger; the existing paper execution
owns *whether it can be filled* - T+1 sellable quantity, limit-up and
limit-down tradability, cost and slippage all stay there. Duplicating any of
that here would give the simulation two answers to the same question.

Every rule is a bound, not a forecast. Position count, per-name weight and new
entries per pass are capped so one loud minute cannot spend the account, and a
name already held is never bought again - a board that stays hot re-qualifies
its leader every minute, and averaging up on repetition is an artefact of the
scan cadence rather than a decision.

Simulation only. No broker client exists anywhere on this path.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Iterable, Mapping

#: One name may hold at most this share of the account's starting equity.
MAX_WEIGHT_PER_NAME = 0.10

#: Above this the ledger stops being a test of the strategy and becomes a test
#: of diversification.
MAX_OPEN_POSITIONS = 8

#: A single pass opens at most this many, so one crowded minute cannot fill the
#: book in one go.
MAX_NEW_PER_PASS = 2

#: A-share board lot.
LOT_SIZE = 100

#: Exits, measured against the simulated average cost.
STOP_LOSS_PCT = -7.0
TAKE_PROFIT_PCT = 18.0


def _number(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed == parsed else None


def position_size(equity: Decimal | float, price: Decimal | float,
                  *, max_weight: float = MAX_WEIGHT_PER_NAME) -> int:
    """Whole board lots for one name, or zero when a lot does not fit."""
    budget = float(equity) * max_weight
    unit = float(price) * LOT_SIZE
    if unit <= 0:
        return 0
    return int(budget // unit) * LOT_SIZE


def exit_reason(position: Mapping[str, Any], price: float,
                *, stop_loss_pct: float = STOP_LOSS_PCT,
                take_profit_pct: float = TAKE_PROFIT_PCT) -> str | None:
    """Why this holding should be closed, if it should."""
    cost = _number(position.get("average_cost"))
    if cost is None or cost <= 0:
        return None
    change = (price - cost) / cost * 100
    if change <= stop_loss_pct:
        return "stop_loss"
    if change >= take_profit_pct:
        return "take_profit"
    return None


def plan_paper_orders(
    candidates: Iterable[Mapping[str, Any]],
    positions: Mapping[str, Mapping[str, Any]],
    quotes: Mapping[str, Mapping[str, Any]],
    *,
    equity: Decimal | float,
    cash: Decimal | float,
    max_open_positions: int = MAX_OPEN_POSITIONS,
    max_new_per_pass: int = MAX_NEW_PER_PASS,
    max_weight: float = MAX_WEIGHT_PER_NAME,
) -> dict[str, Any]:
    """Decide this pass's simulated buys and sells.

    Sells are planned first: freeing a slot before entries are counted is what
    lets a stopped-out name be replaced in the same pass rather than the next.
    """
    sells: list[dict[str, Any]] = []
    held = dict(positions)
    for symbol, position in sorted(positions.items()):
        quantity = int(_number(position.get("sellable_quantity")) or 0)
        price = _number((quotes.get(symbol) or {}).get("price"))
        if quantity <= 0 or price is None:
            continue
        reason = exit_reason(position, price)
        if reason is None:
            continue
        sells.append({
            "symbol": symbol, "side": "sell", "quantity": quantity, "reason": reason,
            "price": price, "average_cost": _number(position.get("average_cost")),
        })
        held.pop(symbol, None)

    open_slots = max(0, max_open_positions - len(held))
    buys: list[dict[str, Any]] = []
    remaining_cash = float(cash)
    skipped: list[dict[str, Any]] = []
    for candidate in candidates:
        symbol = str(candidate.get("symbol") or "")
        if not symbol:
            continue
        if str(candidate.get("direction") or "") != "inflow":
            skipped.append({"symbol": symbol, "reason": "not_an_inflow_candidate"})
            continue
        if not ((candidate.get("large_order") or {}).get("confirmed")):
            skipped.append({"symbol": symbol, "reason": "large_order_unconfirmed"})
            continue
        if symbol in held:
            # A hot board re-qualifies its leader every minute; averaging up on
            # that is the scan cadence talking, not a decision.
            skipped.append({"symbol": symbol, "reason": "already_held"})
            continue
        if len(buys) >= min(max_new_per_pass, open_slots):
            skipped.append({"symbol": symbol, "reason": "position_budget_exhausted"})
            continue
        price = _number((quotes.get(symbol) or {}).get("price"))
        if price is None or price <= 0:
            skipped.append({"symbol": symbol, "reason": "no_usable_price"})
            continue
        quantity = position_size(equity, price, max_weight=max_weight)
        if quantity <= 0:
            skipped.append({"symbol": symbol, "reason": "one_lot_exceeds_the_per_name_weight"})
            continue
        notional = price * quantity
        if notional > remaining_cash:
            skipped.append({"symbol": symbol, "reason": "insufficient_paper_cash"})
            continue
        remaining_cash -= notional
        buys.append({
            "symbol": symbol, "side": "buy", "quantity": quantity, "price": price,
            "notional": round(notional, 2), "board_label": candidate.get("board_label"),
            "relative_strength_pct": candidate.get("relative_strength_pct"),
            "reason": "board_flow_drill_confirmed",
        })
    return {
        "sells": sells, "buys": buys, "skipped": skipped,
        "open_positions": len(held), "open_slots": open_slots,
        "cash_after_plan": round(remaining_cash, 2),
        "boundary": "paper simulation only; no broker client on this path",
    }


__all__ = [
    "LOT_SIZE", "MAX_NEW_PER_PASS", "MAX_OPEN_POSITIONS", "MAX_WEIGHT_PER_NAME",
    "STOP_LOSS_PCT", "TAKE_PROFIT_PCT",
    "exit_reason", "plan_paper_orders", "position_size",
]
