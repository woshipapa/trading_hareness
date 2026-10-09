from __future__ import annotations

import unittest
from datetime import date
from types import SimpleNamespace

from app.strategy_pattern_mining_service import (
    StrategyPatternMiningDependencies,
    run_strategy_pattern_mining,
)


class StrategyPatternMiningServiceTests(unittest.IsolatedAsyncioTestCase):
    def _request(self, **overrides):
        values = {"as_of_date": None, "refresh_limit_sources": False, "max_symbols": 8,
                  "per_cohort": 2, "focus_symbols": None}
        return SimpleNamespace(**{**values, **overrides})

    def _dependencies(self, *, latest_date, candidates, capabilities, fetch_minutes, persist_run, evidence=None):
        async def run_database(action, *args, **_kwargs):
            return action(*args)

        async def limit_evidence(as_of_date):
            return evidence or {"status": "completed", "trade_date": as_of_date.isoformat()}

        def no_health(*_args):
            return None

        return StrategyPatternMiningDependencies(
            latest_date=latest_date, limit_evidence=limit_evidence, sample_candidates=candidates,
            open_provider_capabilities=capabilities, minute_capability="intraday_minute",
            minute_source="tencent_free", fetch_minutes=fetch_minutes,
            intraday_pattern=lambda _rows, _daily: {"status": "completed", "pattern_tags": []},
            review_score=lambda _item, _pattern, _flags: {"review_score": 0, "review_tier": "research_sample"},
            persist_minute_health=no_health, persist_run=persist_run, run_database=run_database,
            model_version="test-v1", handled_errors=(TimeoutError, ValueError),
        )

    async def test_blocks_without_any_persisted_daily_bar(self):
        dependencies = self._dependencies(
            latest_date=lambda: None, candidates=lambda *_args: {},
            capabilities=lambda *_args: None, fetch_minutes=lambda _symbol: None,
            persist_run=lambda *_args: "unexpected",
        )
        result = await run_strategy_pattern_mining(self._request(), dependencies)
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["samples"], [])

    async def test_open_minute_circuit_stores_research_only_blocked_sample_without_fetching(self):
        fetched: list[str] = []
        persisted: list[tuple] = []
        candidate = {
            "symbol": "000001.SZ", "name": "样本", "primary_cohort": "limit_pool",
            "cohorts": ["limit_pool"], "board_context": {}, "limit_context": {},
            "daily_features": {}, "risk_flags": [],
        }

        async def capabilities(_provider, _apis):
            return {"intraday_minute"}

        async def fetch_minutes(symbol):
            fetched.append(symbol)
            return []

        def persist_run(*args):
            persisted.append(args)
            return "run-1"

        dependencies = self._dependencies(
            latest_date=lambda: date(2026, 8, 21),
            candidates=lambda *_args: {"candidates": [candidate], "cohort_counts": {"limit_pool": 1}},
            capabilities=capabilities, fetch_minutes=fetch_minutes, persist_run=persist_run,
        )
        result = await run_strategy_pattern_mining(self._request(), dependencies)

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(fetched, [])
        self.assertEqual(result["samples"][0]["risk_flags"], ["minute_replay_circuit_open"])
        self.assertEqual(persisted[0][2], "blocked")

    async def test_requested_limit_evidence_and_pool_provenance_come_from_the_rows(self):
        positive = {
            "symbol": "603533.SH", "name": "掌阅科技", "primary_cohort": "consecutive_limit",
            "cohorts": ["consecutive_limit"], "board_context": {},
            "limit_context": {"sample_role": "positive_limit_pool", "provider_key": "market_events:fuyao_ths"},
            "daily_features": {}, "risk_flags": [],
        }
        control = {**positive, "symbol": "000002.SZ",
                   "limit_context": {"sample_role": "matched_near_limit_control", "provider_key": "canonical"}}
        evidence = {"status": "blocked", "provider_requests": 0, "reason": "no limit-up pool snapshot"}

        async def capabilities(_provider, _apis):
            return set()

        async def fetch_minutes(_symbol):
            return [{"time": "09:30"}]

        dependencies = self._dependencies(
            latest_date=lambda: date(2026, 9, 18),
            candidates=lambda *_args: {"candidates": [positive, control], "cohort_counts": {}},
            capabilities=capabilities, fetch_minutes=fetch_minutes, persist_run=lambda *_args: "run-2",
            evidence=evidence,
        )
        result = await run_strategy_pattern_mining(self._request(refresh_limit_sources=True), dependencies)

        self.assertEqual(result["source_status"]["limit_sources"], evidence)
        self.assertEqual(result["summary"]["input_provenance"]["limit_pool"], ["market_events:fuyao_ths"])


if __name__ == "__main__":
    unittest.main()
