"""Paper-ledger auto-execution for the board-flow drill's delivered leaders.

Confirms the shortlist with large-order evidence, plans against the paper
ledger (sizing, exits, tradability) and places the orders - in the paper
ledger only; nothing here can reach a broker. Opt-in through
QUANT_PAPER_AUTO_EXECUTION_ENABLED. Moved out of main.py (docs/decisions/0008).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from .paper_order_bridge import STAGE as PAPER_AUTO_STAGE


@dataclass(frozen=True)
class PaperAutoExecutionDependencies:
    enabled: Callable[[], bool]
    database: Any
    run_database: Callable[..., Awaitable[Any]]
    run_vendor_blocking: Callable[..., Awaitable[Any]]
    vendor_source: Callable[[], Any]
    all_a_snapshot: Callable[[], Awaitable[tuple[list[dict[str, Any]], Any]]]
    json_safe: Callable[[Any], Any]
    account_equity: float


async def run_paper_auto_execution(
    candidates: list[dict[str, Any]], quotes: dict[str, Any] | None, deps: PaperAutoExecutionDependencies,
) -> dict[str, Any]:
    """Confirm the shortlist, plan against the ledger, and place the orders.

    Large-order confirmation runs only on what delivery selection kept, because
    it costs one gateway call per name. Everything below it - sizing, exits,
    tradability - already belongs to the strategy and the ledger.
    """
    if not deps.enabled():
        return {"status": "disabled", "reason": "QUANT_PAPER_AUTO_EXECUTION_ENABLED is not set"}
    from .board_flow_drill import select_for_delivery
    from .large_order_confirmation import confirm_candidates
    from .paper_auto_execution import plan_paper_orders
    from .paper_order_bridge import execute_plan
    from .paper_execution import persist_paper_decision
    from .paper_execution_service import accept_paper_decision

    def already_delivered() -> list[str]:
        # The drill's delivery key is carried on each placed order; comparing
        # it with the order's own signal_key never matched, so a hot board's
        # leader was re-confirmed against the gateway every minute.
        with deps.database.transaction() as connection:
            rows = connection.execute(
                """SELECT DISTINCT conditions->>'delivery_key' AS delivery_key
                     FROM quant.intraday_signal_events
                    WHERE stage=%s AND conditions ? 'delivery_key'
                      AND observed_at::date = (now() AT TIME ZONE 'Asia/Shanghai')::date""",
                (PAPER_AUTO_STAGE,),
            ).fetchall()
            return [str(dict(row)["delivery_key"]) for row in rows if dict(row)["delivery_key"]]

    # Only inflow leaders can be bought; an outflow name would spend the
    # session budget and a gateway confirmation before the planner drops it.
    buyable = [candidate for candidate in candidates if str(candidate.get("direction") or "inflow") == "inflow"]
    confirmed: list[dict[str, Any]] = []
    suppressed_repeat = 0
    if buyable:
        delivered = await deps.run_database(already_delivered, timeout_seconds=60)
        chosen = select_for_delivery(buyable, delivered)
        suppressed_repeat = chosen["suppressed_repeat"]
        if chosen["selected"]:
            source = deps.vendor_source()
            confirmed = await deps.run_vendor_blocking(
                lambda: confirm_candidates(chosen["selected"], source.raw_call), timeout_seconds=90)

    def read_account() -> tuple[dict[str, Any] | None, dict[str, dict[str, Any]]]:
        with deps.database.transaction() as connection:
            account = connection.execute(
                "SELECT cash FROM quant.paper_accounts WHERE account_key='default'").fetchone()
            # The stop a strategy set at entry travels with the holding, so an
            # exit is still evaluated on a pass where that strategy is silent.
            positions = connection.execute(
                """SELECT p.symbol,p.quantity,p.sellable_quantity,p.average_cost,p.buy_date,
                          (SELECT (d.evidence->>'stop_loss_pct')::numeric
                             FROM quant.paper_decisions d
                            WHERE d.symbol=p.symbol AND d.direction=1 AND d.status='accepted'
                              AND jsonb_typeof(d.evidence->'stop_loss_pct')='number'
                            ORDER BY d.decision_at DESC LIMIT 1) AS entry_stop_loss_pct
                     FROM quant.paper_positions p WHERE p.quantity>0"""
            ).fetchall()
            return (dict(account) if account else None,
                    {str(dict(row)["symbol"]): dict(row) for row in positions})

    account, positions = await deps.run_database(read_account, timeout_seconds=60)
    if account is None:
        return {"status": "blocked", "reason": "no paper account is configured"}
    if not confirmed and not positions:
        return {"status": "idle", "reason": "no new candidates and no open paper positions",
                "suppressed_repeat": suppressed_repeat}
    if quotes is None:
        rows, _status = await deps.all_a_snapshot()
        quotes = {str(row["symbol"]): row for row in rows if row.get("symbol")}
    cash = float(account["cash"])
    plan = plan_paper_orders(confirmed, positions, quotes,
                             equity=deps.account_equity, cash=cash,
                             session_date=datetime.now(timezone.utc).astimezone(ZoneInfo("Asia/Shanghai")).date())
    if not plan["buys"] and not plan["sells"]:
        return {"status": "no_orders", "skipped": plan["skipped"][:4], "cash": cash}
    observed_at = datetime.now(timezone.utc)

    def place() -> dict[str, Any]:
        with deps.database.transaction() as connection:
            return execute_plan(
                connection, plan, observed_at,
                persist_decision=persist_paper_decision,
                accept_decision=accept_paper_decision,
                json_safe=deps.json_safe,
            )

    placed = await deps.run_database(place, timeout_seconds=180)
    return {"status": "executed", "plan": {"buys": plan["buys"], "sells": plan["sells"]},
            "placed": placed, "cash_before": cash}


__all__ = ["PaperAutoExecutionDependencies", "run_paper_auto_execution"]
