"""Place the planned simulated orders on the existing paper ledger.

The plan says what to trade; this is what makes it real in the ledger. It
deliberately goes through the same two steps a human acceptance uses -
``persist_paper_decision`` then ``accept_paper_decision`` - so the simulation
has exactly one implementation of T+1 sellable quantity, limit-up and
limit-down tradability, cost and slippage. A shortcut that wrote positions
directly would be a second, quieter set of rules.

Every order is anchored to a signal event, because paper_decisions requires
one and because a filled order with no evidence behind it cannot be reviewed
later. Research-staged events are used so nothing on the decision path can
mistake them for watchlist alerts.

Simulation only. No broker client exists anywhere on this path.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Callable, Mapping

#: Kept distinct from the watchlist stages so a decision-path consumer that
#: selects on those can never pick these up.
STAGE = "paper_auto_execution"


def signal_key(order: Mapping[str, Any]) -> str:
    """One key per name, strategy and side, so a repeat is an upsert not a twin."""
    return ":".join((
        str(order.get("symbol") or ""),
        str(order.get("strategy_key") or "unknown"),
        str(order.get("side") or ""),
    ))


def persist_signal_event(connection: Any, order: Mapping[str, Any],
                         observed_at: datetime, *, json_safe: Callable[[Any], Any]) -> uuid.UUID:
    """Record the evidence an order was placed on, before placing it."""
    event_id = uuid.uuid4()
    connection.execute(
        """INSERT INTO quant.intraday_signal_events(
                signal_event_id,scan_id,symbol,signal_key,signal_type,severity,state,score,
                observed_at,conditions,evidence,risk_flags,stage)
           VALUES(%s,NULL,%s,%s,%s,'info','confirmed',0,%s,%s,%s,%s,%s)""",
        (event_id, order["symbol"], signal_key(order),
         "entry" if order.get("side") == "buy" else "exit",
         observed_at, json_safe(dict(order)), json_safe({"boundary": "paper_simulation_only"}),
         json_safe(list(order.get("risk_flags") or [])), STAGE),
    )
    return event_id


def decision_payload(order: Mapping[str, Any], observed_at: datetime) -> dict[str, Any]:
    """The proposal the ledger's acceptance step consumes."""
    return {
        "strategy_key": str(order.get("strategy_key") or "unknown"),
        "strategy_version": "paper-auto-execution-v1",
        "symbol": str(order["symbol"]),
        "direction": 1 if order.get("side") == "buy" else -1,
        "status": "proposed",
        "decision_at": observed_at,
        "target_quantity": int(order.get("quantity") or 0),
        "target_weight": float(order.get("entry_fraction") or 0),
        "evidence": {
            "reason": order.get("reason"), "mode": order.get("mode"),
            "sizing_source": order.get("sizing_source"),
            "stop_loss_pct": order.get("stop_loss_pct"),
            "exit_codes": order.get("exit_codes") or [],
            "board_label": order.get("board_label"),
            "relative_strength_pct": order.get("relative_strength_pct"),
            "boundary": "paper_simulation_only_no_broker",
        },
        "risk_flags": [*list(order.get("risk_flags") or []), "paper_only", "auto_executed"],
    }


def place_order(
    connection: Any,
    order: Mapping[str, Any],
    observed_at: datetime,
    *,
    persist_decision: Callable[..., bool],
    accept_decision: Callable[..., dict[str, Any]],
    json_safe: Callable[[Any], Any],
    account_key: str = "default",
) -> dict[str, Any]:
    """Anchor, propose and accept one simulated order.

    The acceptance may still refuse it - a limit-up name cannot be bought, a
    T+1 holding cannot be sold - and that refusal is the ledger's answer, kept
    rather than retried here.
    """
    event_id = persist_signal_event(connection, order, observed_at, json_safe=json_safe)
    payload = decision_payload(order, observed_at)
    if not persist_decision(connection, event_id, payload):
        return {"symbol": order["symbol"], "status": "duplicate",
                "reason": "an identical decision already exists for this event"}
    row = connection.execute(
        """SELECT decision_id FROM quant.paper_decisions
            WHERE signal_event_id=%s AND strategy_key=%s AND strategy_version=%s""",
        (event_id, payload["strategy_key"], payload["strategy_version"]),
    ).fetchone()
    if row is None:
        return {"symbol": order["symbol"], "status": "failed", "reason": "decision was not stored"}
    result = accept_decision(
        connection, decision_id=dict(row)["decision_id"],
        quantity=int(order.get("quantity") or 0), accepted_at=observed_at, account_key=account_key,
    )
    return {
        "symbol": order["symbol"], "side": order.get("side"),
        "strategy_key": payload["strategy_key"], "quantity": int(order.get("quantity") or 0),
        "signal_event_id": str(event_id), "status": "submitted", "ledger": result,
    }


def execute_plan(
    connection: Any,
    plan: Mapping[str, Any],
    observed_at: datetime,
    *,
    persist_decision: Callable[..., bool],
    accept_decision: Callable[..., dict[str, Any]],
    json_safe: Callable[[Any], Any],
    account_key: str = "default",
) -> dict[str, Any]:
    """Place every order in a plan, sells before buys.

    One order failing does not stop the rest: a name the ledger refuses says
    nothing about the next one, and abandoning the pass would silently drop
    exits that were already due.
    """
    placed: list[dict[str, Any]] = []
    for order in [*plan.get("sells", []), *plan.get("buys", [])]:
        try:
            placed.append(place_order(
                connection, order, observed_at, persist_decision=persist_decision,
                accept_decision=accept_decision, json_safe=json_safe, account_key=account_key,
            ))
        except Exception as error:  # noqa: BLE001 - one refusal must not end the pass
            placed.append({"symbol": order.get("symbol"), "side": order.get("side"),
                           "status": "failed", "reason": f"{type(error).__name__}: {str(error)[:160]}"})
    return {
        "placed": placed,
        "submitted": sum(1 for item in placed if item.get("status") == "submitted"),
        "failed": sum(1 for item in placed if item.get("status") == "failed"),
        "boundary": "paper simulation only; no broker client on this path",
    }


__all__ = ["STAGE", "decision_payload", "execute_plan", "persist_signal_event",
           "place_order", "signal_key"]
