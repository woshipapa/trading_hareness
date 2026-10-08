"""Paper auto-execution, moved out of main.py (docs/decisions/0008); it had no direct test."""

from __future__ import annotations

import asyncio
import unittest
from contextlib import contextmanager
from unittest.mock import patch

from app.paper_auto_execution_runtime import PaperAutoExecutionDependencies, run_paper_auto_execution


class _Result:
    def __init__(self, rows):
        self.rows = rows

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows


class _Connection:
    def __init__(self, account, positions):
        self.account, self.positions = account, positions

    def execute(self, sql, _params=None):
        if "paper_accounts" in sql:
            return _Result([self.account] if self.account else [])
        if "paper_positions" in sql:
            return _Result(self.positions)
        return _Result([])  # already-delivered keys


class _Database:
    def __init__(self, account=None, positions=()):
        self.connection = _Connection(account, list(positions))

    @contextmanager
    def transaction(self):
        yield self.connection


def deps(*, enabled=True, database=None, quotes=None):
    async def run(action, *args, **_kwargs):
        return action(*args)

    async def snapshot():
        return list((quotes or {}).values()), {}

    return PaperAutoExecutionDependencies(
        enabled=lambda: enabled, database=database or _Database(), run_database=run, run_vendor_blocking=run,
        vendor_source=lambda: type("Source", (), {"raw_call": None})(), all_a_snapshot=snapshot,
        json_safe=lambda value: value, account_equity=1_000_000.0,
    )


class PaperAutoExecutionTests(unittest.TestCase):
    def test_disabled_does_nothing(self):
        result = asyncio.run(run_paper_auto_execution([{"symbol": "000001.SZ"}], None, deps(enabled=False)))
        self.assertEqual(result["status"], "disabled")

    def test_without_an_account_it_is_blocked(self):
        result = asyncio.run(run_paper_auto_execution([], None, deps(database=_Database(account=None))))
        self.assertEqual(result, {"status": "blocked", "reason": "no paper account is configured"})

    def test_no_candidates_and_no_positions_is_idle(self):
        result = asyncio.run(run_paper_auto_execution([], None, deps(database=_Database(account={"cash": 5000}))))
        self.assertEqual(result["status"], "idle")

    def test_outflow_names_are_never_confirmed_and_a_plan_is_placed(self):
        candidates = [{"symbol": "000001.SZ", "direction": "inflow"}, {"symbol": "000002.SZ", "direction": "outflow"}]
        quotes = {"000001.SZ": {"symbol": "000001.SZ", "price": 10.0}}
        seen = {}

        def select(buyable, _delivered):
            seen["buyable"] = [item["symbol"] for item in buyable]
            return {"selected": buyable, "suppressed_repeat": 0}

        with patch("app.board_flow_drill.select_for_delivery", side_effect=select), \
                patch("app.large_order_confirmation.confirm_candidates", side_effect=lambda chosen, _call: chosen), \
                patch("app.paper_auto_execution.plan_paper_orders",
                      return_value={"buys": [{"symbol": "000001.SZ"}], "sells": [], "skipped": []}), \
                patch("app.paper_order_bridge.execute_plan", return_value={"orders": 1}):
            result = asyncio.run(run_paper_auto_execution(
                candidates, quotes, deps(database=_Database(account={"cash": 100000}))))
        self.assertEqual(seen["buyable"], ["000001.SZ"])
        self.assertEqual(result["status"], "executed")
        self.assertEqual(result["placed"], {"orders": 1})
        self.assertEqual(result["cash_before"], 100000.0)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
