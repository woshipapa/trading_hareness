from __future__ import annotations

import unittest
from datetime import date

from app.post_close_refresh import record_stage_with_receipt, run_refresh
from app.post_close_refresh_service import (
    POST_CLOSE_STAGE_DEPENDENCIES,
    POST_CLOSE_STAGE_ORDER,
    POST_CLOSE_TIMEOUT_OVERRIDES,
    PostCloseRefreshDependencies,
    run_post_close_refresh,
)
from app.request_models import PostCloseRefreshRequest


class PostCloseRefreshTests(unittest.IsolatedAsyncioTestCase):
    async def test_outcome_settlement_has_explicit_long_bounded_budget(self):
        self.assertEqual(POST_CLOSE_TIMEOUT_OVERRIDES["analyst_outcomes"], 300.0)
        self.assertEqual(POST_CLOSE_TIMEOUT_OVERRIDES["analyst_intraday_outcomes"], 180.0)

    async def test_service_assembles_same_date_stages_and_announcements_after_core_symbols(self):
        captured: dict[str, object] = {}
        providers: dict[str, object] = {}
        longhu_enabled = {"value": False}

        async def completed(*_args, **_kwargs):
            return {"status": "completed"}

        async def load_core(limit: int):
            captured["core_limit"] = limit
            return ["000001.SZ"]

        async def probe(request):
            captured["probe"] = request
            return {"status": "completed"}

        async def announcements(request):
            captured["announcements"] = request
            return {"status": "completed"}

        async def full_market_daily(request):
            captured["daily_request"] = request
            return {"status": "completed"}

        async def calendar():
            captured["calendar"] = True
            return {"status": "completed"}

        async def orchestrator(_request, **kwargs):
            captured["stage_order"] = kwargs["stage_order"]
            captured["dependencies"] = kwargs["stage_dependencies"]
            await kwargs["actions"]["trade_calendar"]()
            await kwargs["actions"]["full_market_daily"]()
            await kwargs["actions"]["akshare_supplements"]()
            await kwargs["actions"]["cninfo_announcements"]()
            captured["ledger"] = await kwargs["actions"]["candidate_ledger"]()
            return {"status": "completed", "stages": {}}

        dependencies = PostCloseRefreshDependencies(
            database=object(), china_today=lambda: date(2026, 8, 21),
            longhu_configured=lambda: longhu_enabled["value"],
            longhu_close_context=lambda _day: {"status": "completed"}, provider_configs=lambda: providers,
            run_database=completed, reconcile_stale_fetch_runs=lambda *_: None,
            reprocess_remote_reports=lambda *_: None, sync_market_universe=completed,
            sync_full_market_daily=full_market_daily, sync_strategy_index_context=completed,
            build_market_snapshot=completed, load_core_symbols=load_core, akshare_probe=probe,
            sync_ths_industry_flow=completed, sync_ths_concept_flow=completed,
            rebuild_market_flow_features=lambda *_: None, limit_evidence=completed,
            persist_settled_limit_pool=lambda *_: {"status": "completed"},
            run_pattern_mining=completed, sync_daily_controls=completed,
            sync_cninfo_announcements=announcements, run_board_report=completed,
            run_strategy_decision=completed, persist_close_review=lambda *_: None,
            recompute_outcomes=lambda *_: None, recompute_intraday_outcomes=lambda *_: None,
            recompute_scorecards=lambda *_: None, rebuild_analyst_research=lambda *_: None,
            run_post_close_strategy=lambda *_: None, refresh_decision_research=lambda *_: {"status": "completed"},
            persist_watchlist_main_wave=lambda *_: None,
            build_research_snapshot=lambda *_: None, run_orchestrator=orchestrator,
            record_stage=completed, lease_key="lease", lease_seconds=lambda: 60,
            acquire_lease=lambda *_: True, renew_lease=lambda *_: True, release_lease=lambda *_: None,
            safe_error_detail=lambda value, _limit: value, json_safe=lambda value: value,
            sync_forward_calendar=calendar,
            materialize_candidate_ledger=lambda day: {"post_close_base_ready": 3, "day": day},
        )

        result = await run_post_close_refresh(
            PostCloseRefreshRequest(trade_date=date(2026, 8, 21), announcement_limit=7), dependencies,
        )
        self.assertTrue(captured["calendar"], "the forward calendar stage runs the wired sync")
        self.assertEqual(POST_CLOSE_STAGE_ORDER[:2], ("stale_fetch_runs", "trade_calendar"))
        self.assertEqual(POST_CLOSE_STAGE_ORDER[-2:], ("candidate_ledger", "minute_panel_export"),
                         "the ledger runs after every strategy stage; the minute panel export closes the run")
        self.assertEqual(captured["ledger"], {"status": "completed"},
                         "the ledger stage is wired and runs through run_database")
        self.assertIn("post_close_strategy", POST_CLOSE_STAGE_DEPENDENCIES["candidate_ledger"])
        self.assertNotIn("trade_calendar", POST_CLOSE_STAGE_DEPENDENCIES)

        self.assertEqual(result["status"], "completed")
        self.assertEqual(captured["stage_order"], POST_CLOSE_STAGE_ORDER)
        self.assertEqual(captured["dependencies"], POST_CLOSE_STAGE_DEPENDENCIES)
        self.assertEqual(captured["core_limit"], 7)
        self.assertEqual(captured["daily_request"].provider, "auto")
        self.assertEqual(captured["probe"].symbol, "000001.SZ")
        self.assertEqual(captured["announcements"].symbols, ["000001.SZ"])
        self.assertEqual(captured["announcements"].start_date, date(2026, 7, 7))

        class PromaxDaily:
            configured = True
            get_gateway_mode = "promax"

            @staticmethod
            def supports(api_name: str) -> bool:
                return api_name == "daily"

        providers["super_get"] = PromaxDaily()
        await run_post_close_refresh(
            PostCloseRefreshRequest(trade_date=date(2026, 8, 21), announcement_limit=7), dependencies,
        )
        self.assertEqual(captured["daily_request"].provider, "super_get")

        # A configured Longhu owner must enter the Longhu auto route even when
        # a Promax Super GET capability is also present.
        longhu_enabled["value"] = True
        await run_post_close_refresh(
            PostCloseRefreshRequest(trade_date=date(2026, 8, 21), announcement_limit=7), dependencies,
        )
        self.assertEqual(captured["daily_request"].provider, "auto")

    async def test_close_snapshot_requests_provider_refresh_for_owner_fallback_chain(self):
        captured: dict[str, object] = {}

        async def build_snapshot(request):
            captured["snapshot_request"] = request
            return {"status": "completed"}

        async def run_db(*_args, **_kwargs):
            return {"status": "completed"}

        dependencies = PostCloseRefreshDependencies(
            database=object(), china_today=lambda: date(2026, 8, 21), longhu_configured=lambda: True,
            longhu_close_context=lambda _day: {"status": "completed"}, provider_configs=lambda: {},
            run_database=run_db, reconcile_stale_fetch_runs=lambda *_: None,
            reprocess_remote_reports=lambda *_: None, sync_market_universe=run_db,
            sync_full_market_daily=run_db, sync_strategy_index_context=run_db,
            build_market_snapshot=build_snapshot, load_core_symbols=lambda _limit: [], akshare_probe=run_db,
            sync_ths_industry_flow=run_db, sync_ths_concept_flow=run_db,
            rebuild_market_flow_features=lambda *_: None, limit_evidence=run_db,
            persist_settled_limit_pool=lambda *_: {"status": "completed"}, run_pattern_mining=run_db,
            sync_daily_controls=run_db, sync_cninfo_announcements=run_db, run_board_report=run_db,
            run_strategy_decision=run_db, persist_close_review=lambda *_: None,
            recompute_outcomes=lambda *_: None, recompute_intraday_outcomes=lambda *_: None,
            recompute_scorecards=lambda *_: None, rebuild_analyst_research=lambda *_: None,
            run_post_close_strategy=lambda *_: None, refresh_decision_research=lambda *_: {"status": "completed"},
            persist_watchlist_main_wave=lambda *_: None, build_research_snapshot=lambda *_: None,
            run_orchestrator=lambda request, **kwargs: kwargs["actions"]["close_market_snapshot"](),
            record_stage=run_db, lease_key="lease", lease_seconds=lambda: 60,
            acquire_lease=lambda *_: True, renew_lease=lambda *_: True, release_lease=lambda *_: None,
            safe_error_detail=lambda value, _limit: value, json_safe=lambda value: value,
        )

        await run_post_close_refresh(
            PostCloseRefreshRequest(trade_date=date(2026, 8, 21), announcement_limit=7), dependencies,
        )
        self.assertTrue(captured["snapshot_request"].refresh_public_quotes)

    async def test_limit_ladder_reports_captured_evidence_instead_of_fetching(self):
        """Without Longhu the stage only reads the session's captured pools (decision 0005)."""
        evidence_days: list[date] = []
        settled_days: list[date] = []

        async def run_db(action, *args, **_kwargs):
            result = action(*args)
            return await result if hasattr(result, "__await__") else result

        async def limit_evidence(day):
            evidence_days.append(day)
            return {"status": "blocked", "provider_requests": 0,
                    "reason": "no limit-up pool snapshot was captured for this date"}

        async def ladder_only(_request, **kwargs):
            return await kwargs["actions"]["limit_ladder"]()

        longhu = {"value": False}
        dependencies = PostCloseRefreshDependencies(
            database=object(), china_today=lambda: date(2026, 9, 18), longhu_configured=lambda: longhu["value"],
            longhu_close_context=lambda _day: {}, provider_configs=lambda: {}, run_database=run_db,
            reconcile_stale_fetch_runs=lambda *_: None, reprocess_remote_reports=lambda *_: None,
            sync_market_universe=run_db, sync_full_market_daily=run_db, sync_strategy_index_context=run_db,
            build_market_snapshot=run_db, load_core_symbols=lambda _limit: [], akshare_probe=run_db,
            sync_ths_industry_flow=run_db, sync_ths_concept_flow=run_db,
            rebuild_market_flow_features=lambda *_: None, limit_evidence=limit_evidence,
            persist_settled_limit_pool=lambda _db, day: settled_days.append(day) or {"status": "completed"},
            run_pattern_mining=run_db, sync_daily_controls=run_db, sync_cninfo_announcements=run_db,
            run_board_report=run_db, run_strategy_decision=run_db, persist_close_review=lambda *_: None,
            recompute_outcomes=lambda *_: None, recompute_intraday_outcomes=lambda *_: None,
            recompute_scorecards=lambda *_: None, rebuild_analyst_research=lambda *_: None,
            run_post_close_strategy=lambda *_: None, refresh_decision_research=lambda *_: {},
            persist_watchlist_main_wave=lambda *_: None, build_research_snapshot=lambda *_: None,
            run_orchestrator=ladder_only, record_stage=run_db, lease_key="lease", lease_seconds=lambda: 60,
            acquire_lease=lambda *_: True, renew_lease=lambda *_: True, release_lease=lambda *_: None,
            safe_error_detail=lambda value, _limit: value, json_safe=lambda value: value,
        )
        request = PostCloseRefreshRequest(trade_date=date(2026, 9, 18))

        stage = await run_post_close_refresh(request, dependencies)
        self.assertEqual(stage["status"], "blocked")
        self.assertEqual(stage["provider_requests"], 0)
        self.assertEqual(evidence_days, [date(2026, 9, 18)])
        self.assertEqual(POST_CLOSE_STAGE_DEPENDENCIES["limit_lift_pattern_mining"][-1], "limit_ladder")

        longhu["value"] = True
        await run_post_close_refresh(request, dependencies)
        self.assertEqual((evidence_days, settled_days), ([date(2026, 9, 18)], [date(2026, 9, 18)]))

    async def test_optional_stage_receipt_wrapper_is_used(self):
        seen: list[str] = []

        async def run_db(action, *args, **kwargs):
            result = action(*args)
            return await result if hasattr(result, "__await__") else result

        async def record_stage(name, day, action):
            seen.append(f"{name}:{day}")
            result = action()
            return await result if hasattr(result, "__await__") else result

        result = await run_refresh(
            object(), db=object(), lease_key="lease", lease_seconds=lambda: 60,
            run_database_blocking=run_db,
            acquire_lease=lambda *_: True,
            renew_lease=lambda *_: True,
            release_lease=lambda *_: None,
            actions={"one": lambda: {"status": "completed"}}, stage_order=("one",),
            trade_date=date(2026, 8, 21), safe_error_detail=lambda value, _limit: value,
            json_safe=lambda value: value, record_stage=record_stage,
        )
        self.assertEqual(result["status"], "completed")
        self.assertEqual(seen, ["one:2026-08-21"])

    async def test_core_daily_controls_is_a_real_post_close_stage(self):
        calls: list[str] = []

        async def run_db(action, *args, **_kwargs):
            return action(*args)

        result = await run_refresh(
            object(), db=object(), lease_key="lease", lease_seconds=lambda: 60,
            run_database_blocking=run_db, acquire_lease=lambda *_: True,
            renew_lease=lambda *_: True, release_lease=lambda *_: None,
            actions={"core_daily_controls": lambda: calls.append("controls") or {"status": "completed"}},
            stage_order=("core_daily_controls",), trade_date=date(2026, 8, 21),
            safe_error_detail=lambda value, _limit: value, json_safe=lambda value: value,
        )
        self.assertEqual(calls, ["controls"])
        self.assertEqual(result["stages"]["core_daily_controls"]["status"], "completed")

    async def test_controls_execute_before_dependent_post_close_stages(self):
        calls: list[str] = []

        async def run_db(action, *args, **_kwargs):
            return action(*args)

        result = await run_refresh(
            object(), db=object(), lease_key="lease", lease_seconds=lambda: 60,
            run_database_blocking=run_db, acquire_lease=lambda *_: True,
            renew_lease=lambda *_: True, release_lease=lambda *_: None,
            actions={
                "full_market_daily": lambda: calls.append("daily") or {"status": "completed"},
                "core_daily_controls": lambda: calls.append("controls") or {"status": "completed"},
                "limit_ladder": lambda: calls.append("ladder") or {"status": "completed"},
            },
            stage_order=("full_market_daily", "core_daily_controls", "limit_ladder"),
            trade_date=date(2026, 8, 21), safe_error_detail=lambda value, _limit: value,
            json_safe=lambda value: value,
        )
        self.assertEqual(result["status"], "completed")
        self.assertEqual(calls, ["daily", "controls", "ladder"])

    async def test_dependency_gate_preserves_evidence_but_blocks_strategy_stage(self):
        calls: list[str] = []

        async def run_db(action, *args, **_kwargs):
            return action(*args)

        result = await run_refresh(
            object(), db=object(), lease_key="lease", lease_seconds=lambda: 60,
            run_database_blocking=run_db, acquire_lease=lambda *_: True,
            renew_lease=lambda *_: True, release_lease=lambda *_: None,
            actions={
                "full_market_daily": lambda: calls.append("daily") or {"status": "completed"},
                "controls": lambda: calls.append("controls") or {"status": "blocked", "reason": "coverage"},
                "strategy": lambda: calls.append("strategy") or {"status": "completed"},
                "evidence": lambda: calls.append("evidence") or {"status": "completed"},
            },
            stage_order=("full_market_daily", "controls", "strategy", "evidence"),
            stage_dependencies={"strategy": ("controls",)},
            trade_date=date(2026, 8, 21), safe_error_detail=lambda value, _limit: value,
            json_safe=lambda value: value,
        )
        self.assertEqual(calls, ["daily", "controls", "evidence"])
        self.assertEqual(result["stages"]["strategy"]["status"], "blocked")
        self.assertIn("controls", result["stages"]["strategy"]["reason"])
        self.assertFalse(result["controls_ready"])
        self.assertIn("控制面", result["retry_hint"])

    async def test_completed_stage_receipt_skips_action_after_restart(self):
        class Result:
            def fetchone(self):
                return {"run_id": "receipt-1", "status": "completed", "output_summary": {"status": "completed"}}

        class Connection:
            def execute(self, *_args, **_kwargs):
                return Result()

        class Database:
            def transaction(self):
                class Context:
                    def __enter__(self): return Connection()
                    def __exit__(self, *_args): return False
                return Context()

        async def run_db(action, *args, **_kwargs):
            result = action(*args)
            return await result if hasattr(result, "__await__") else result

        called = False

        def action():
            nonlocal called
            called = True
            return {"status": "completed"}

        result = await record_stage_with_receipt(
            "daily", date(2026, 8, 21), action, db=Database(),
            run_database_blocking=run_db, safe_error_detail=lambda value, _limit: value,
        )
        self.assertFalse(called)
        self.assertTrue(result["resumed_from_receipt"])

    async def test_completed_receipt_repairs_legacy_null_summary_status(self):
        class Result:
            def fetchone(self):
                return {"run_id": "receipt-1", "status": "completed", "output_summary": {"status": None}}

        class Connection:
            def execute(self, *_args, **_kwargs):
                return Result()

        class Database:
            def transaction(self):
                class Context:
                    def __enter__(self): return Connection()
                    def __exit__(self, *_args): return False
                return Context()

        async def run_db(action, *args, **_kwargs):
            return action(*args)

        result = await record_stage_with_receipt(
            "legacy", date(2026, 8, 21), lambda: self.fail("completed receipt reran"),
            db=Database(), run_database_blocking=run_db,
            safe_error_detail=lambda value, _limit: value,
        )
        self.assertEqual(result["status"], "completed")
        self.assertTrue(result["resumed_from_receipt"])


if __name__ == "__main__":
    unittest.main()
