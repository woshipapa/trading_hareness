from __future__ import annotations

import re

from datetime import date, datetime, timedelta
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

from app.post_close_strategy_service import candidates, retry_window, run


class PostCloseStrategyServiceTests(unittest.TestCase):
    def _database(self) -> tuple[MagicMock, MagicMock]:
        database = MagicMock()
        connection = MagicMock()
        database.transaction.return_value.__enter__.return_value = connection
        return database, connection

    def test_candidate_loader_uses_only_persisted_rows_and_exact_context(self) -> None:
        database, connection = self._database()
        coverage_result = MagicMock()
        coverage_result.fetchone.return_value = {"symbols": 2}
        rows_result = MagicMock()
        rows_result.fetchall.return_value = [{"symbol": "000001.SZ", "close": 10}]
        connection.execute.side_effect = [coverage_result, rows_result]
        exact_context = {"000001.SZ": {"sector_key": "885001.TI"}}
        screen = MagicMock(return_value={"status": "completed", "candidates": []})
        as_of_date = date(2026, 8, 14)

        payload = candidates(
            database, as_of_date, 20, 2, board_context=lambda value: exact_context,
            screen=screen, daily_base_structure=lambda values: {},
            forming_structure=lambda values: {}, fresh_start_structure=lambda values: {},
        )

        self.assertEqual(payload["status"], "completed")
        args, kwargs = screen.call_args
        self.assertEqual(args[:4], (as_of_date, 20, 2, 2))
        self.assertEqual(args[4], [{"symbol": "000001.SZ", "close": 10}])
        self.assertEqual(args[5], exact_context)
        self.assertIn("daily_base_structure", kwargs)

    def test_run_preserves_explicit_date_and_persists_candidate_evidence(self) -> None:
        database, connection = self._database()
        latest_result = MagicMock()
        latest_result.fetchone.return_value = {"trading_date": date(2026, 8, 13)}
        inserted_result = MagicMock()
        inserted_result.fetchone.return_value = {"run_id": "c7a3668d-02fc-4d50-8f97-923f7f0f430d"}
        connection.execute.side_effect = [
            latest_result, inserted_result, MagicMock(), MagicMock(), MagicMock(), MagicMock(),
        ]
        target_date = date(2026, 8, 14)
        loaded = {
            "status": "completed", "as_of_date": str(target_date),
            "candidates": [{
                "symbol": "000001.SZ", "candidate_type": "base_ready_30d", "score": 88.0,
                "structure": {"status": "ready"}, "board_context": {"exact_member_mapping": True},
                "risk_flags": [],
            }],
            "source_status": {"daily_bars": 30, "daily_symbols": 5000, "exact_board_context_symbols": 1},
            "screen_observations": [{
                "symbol": "000001.SZ", "name": "A", "screen_state": "candidate",
                "candidate_type": "base_ready_30d", "score": 88.0, "reason_codes": [],
                "structure": {"status": "ready"}, "board_context": {"exact_member_mapping": True},
            }],
            "summary": {"returned": 1},
        }
        loader = MagicMock(return_value=loaded)
        request = SimpleNamespace(as_of_date=target_date, limit=20, minimum_full_market_symbols=5000)

        payload = run(
            database, request, model_version="post-close-test-v1", candidate_loader=loader, json_safe=lambda value: value,
        )

        loader.assert_called_once_with(target_date, 20, 5000)
        self.assertEqual(payload["status"], "completed")
        self.assertEqual(payload["as_of_date"], str(target_date))
        self.assertEqual(payload["model_version"], "post-close-test-v1")
        self.assertEqual(len(payload["screen_observations"]), 1)
        self.assertEqual(payload["source_status"]["screen_observations_persisted"], 1)
        self.assertEqual(payload["source_status"]["screen_observations_returned"], 1)
        self.assertEqual(connection.execute.call_count, 6)

    def test_retry_window_is_shanghai_clock_bounded(self) -> None:
        china = ZoneInfo("Asia/Shanghai")
        self.assertFalse(retry_window(datetime(2026, 8, 14, 18, 54, tzinfo=china)))
        self.assertTrue(retry_window(datetime(2026, 8, 14, 18, 55, tzinfo=china)))
        self.assertTrue(retry_window(datetime(2026, 8, 14, 20, 29, 59, tzinfo=china)))
        # A delayed provider can publish the full daily cross-section after
        # the first evening attempt. Keep the same-date retry window open
        # long enough to consume that late evidence without crossing midnight.
        self.assertTrue(retry_window(datetime(2026, 8, 14, 21, 59, 59, tzinfo=china)))
        self.assertFalse(retry_window(datetime(2026, 8, 14, 22, 0, tzinfo=china)))


class PostCloseStructureSourceTests(unittest.TestCase):
    """Where the structure window comes from, and what it refuses to scale by.

    The screen used to read a separately imported front-adjusted artifact.  On
    2026-09-18 that artifact's newest bar was 2026-09-01, so the ranked bases
    described a shape up to seventeen sessions old while the run reported
    itself complete.  These pin the window to the tables the platform writes
    every session.
    """

    def _statement(self) -> tuple[str, tuple]:
        database = MagicMock()
        connection = MagicMock()
        database.transaction.return_value.__enter__.return_value = connection
        coverage_result = MagicMock()
        coverage_result.fetchone.return_value = {"symbols": 2}
        rows_result = MagicMock()
        rows_result.fetchall.return_value = []
        connection.execute.side_effect = [coverage_result, rows_result]
        candidates(
            database, date(2026, 9, 18), 20, 2, board_context=lambda value: {},
            screen=MagicMock(return_value={"status": "completed", "candidates": []}),
            daily_base_structure=lambda values: {}, forming_structure=lambda values: {},
            fresh_start_structure=lambda values: {},
        )
        statement, parameters = connection.execute.call_args_list[1].args
        return re.sub(r"\s+", " ", statement), parameters

    def test_the_window_is_read_from_the_daily_bars_the_platform_maintains(self):
        statement, _parameters = self._statement()
        self.assertIn("FROM quant.canonical_bars_daily b", statement)
        self.assertIn("JOIN factors f ON f.symbol=b.symbol", statement)

    def test_the_hand_imported_adjusted_artifact_is_no_longer_a_dependency(self):
        statement, _parameters = self._statement()
        self.assertNotIn("research_adjusted_bars_daily", statement)
        self.assertNotIn("stock_brain_tencent_qfq", statement)

    def test_a_same_day_identity_factor_can_never_scale_the_window(self):
        # The licensed close path stores adj_factor=1 as an honest placeholder
        # for a corporate-action history it does not claim.  A window scaled by
        # it would look adjusted while ignoring every split inside it.
        statement, _parameters = self._statement()
        self.assertIn("quant.daily_adjustment_factors", statement)
        self.assertIn("'same_day_identity_only'", statement)
        self.assertIn("IS DISTINCT FROM", statement)

    def test_the_real_factor_is_handed_to_the_adjustment_rather_than_a_constant(self):
        statement, _parameters = self._statement()
        self.assertIn("f.adj_factor", statement)
        self.assertNotIn("1::numeric AS adj_factor", statement)

    def test_every_date_bound_is_the_same_seventy_day_window(self):
        statement, parameters = self._statement()
        as_of, start = date(2026, 9, 18), date(2026, 9, 18) - timedelta(days=70)
        # daily_basic providers and the flow source now come from the catalog;
        # they resolve to exactly what the query used to spell out.
        self.assertEqual(parameters, (as_of, ["tushare_super_get", "longhuvip_composite"], as_of,
                                      "longhuvip_main_net", as_of, start, as_of, as_of, start))
        self.assertIn("quant.daily_fundamentals", statement)
        self.assertNotIn("tushare_raw_records", statement)
        self.assertIn("rn<=30", statement)


if __name__ == "__main__":
    unittest.main()
