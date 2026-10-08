"""The board drill pass, moved out of main.py (docs/decisions/0008); it had no direct test."""

from __future__ import annotations

import asyncio
import unittest
from contextlib import contextmanager
from datetime import datetime, timezone
from unittest.mock import patch

from app import board_flow_drill_runtime as runtime
from app.board_flow_drill_runtime import BoardDrillDependencies, drill_board_stock_candidates


class _Database:
    @contextmanager
    def transaction(self):
        yield object()


def deps(paper_calls):
    async def run(action, *args, **_kwargs):
        return action(*args)

    async def snapshot():
        return [{"symbol": "000001.SZ", "pct_change": 5.0}], {}

    async def paper(candidates, quotes):
        paper_calls.append((candidates, quotes))
        return {"status": "idle"}

    return BoardDrillDependencies(database=_Database(), run_database=run, all_a_snapshot=snapshot,
                                  paper_execution=paper, now=lambda: datetime(2026, 10, 9, 2, tzinfo=timezone.utc))


class BoardDrillRuntimeTests(unittest.TestCase):
    def test_a_quiet_minute_still_lets_paper_positions_be_checked(self):
        calls = []
        result = asyncio.run(drill_board_stock_candidates([{"taxonomy_key": "eastmoney_concept"}], deps(calls)))
        self.assertEqual(result["status"], "idle")
        self.assertEqual(calls, [([], None)])

    def test_no_membership_blocks_the_drill(self):
        calls = []
        with patch.object(runtime, "sector_membership", return_value={}), \
                patch.object(runtime, "instrument_names", return_value={}):
            result = asyncio.run(drill_board_stock_candidates([{"taxonomy_key": "longhu_ths_industry"}], deps(calls)))
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(calls, [([], None)])

    def test_drilled_candidates_are_named_and_handed_to_paper_execution(self):
        calls = []
        with patch.object(runtime, "sector_membership", return_value={"881101": {"000001.SZ"}}), \
                patch.object(runtime, "instrument_names", return_value={"000001.SZ": "平安银行"}), \
                patch.object(runtime, "drill_board_events", return_value={
                    "candidates": [{"symbol": "000001.SZ"}], "boards_drilled": 1, "boards_without_membership": 0}):
            result = asyncio.run(drill_board_stock_candidates([{"taxonomy_key": "longhu_ths_industry"}], deps(calls)))
        self.assertEqual(result["status"], "completed")
        self.assertFalse(result["decision_eligible"])
        self.assertEqual(calls[0][0][0]["symbol"], "000001.SZ")
        self.assertIn("000001.SZ", calls[0][1])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
