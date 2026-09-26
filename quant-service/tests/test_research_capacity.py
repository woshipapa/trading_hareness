from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from app.owner_storage import TIERED_EVIDENCE_TABLES
from app.research_capacity import current_data_coverage, feature_readiness_projection


class FeatureReadinessProjectionTests(unittest.TestCase):
    def test_partial_enrichment_does_not_block_complete_p0_daily_baseline(self):
        result = feature_readiness_projection([
            {"feature": "daily_bars", "symbols": 1_000, "rows": 10_000},
            {"feature": "daily_basic", "symbols": 1_000, "rows": 10_000},
            {"feature": "trade_limits", "symbols": 1_000, "rows": 10_000},
            {"feature": "moneyflow", "symbols": 5, "rows": 50},
            {"feature": "announcements", "symbols": 0, "rows": 0},
        ], 1_000)
        self.assertTrue(result["decision_ready"])
        self.assertEqual(result["blockers"], [])
        self.assertEqual(result["supplementary_partial"], ["moneyflow", "announcements"])

    def test_missing_required_daily_input_remains_hard_blocker(self):
        result = feature_readiness_projection([
            {"feature": "daily_bars", "symbols": 1_000, "rows": 10_000},
            {"feature": "daily_basic", "symbols": 799, "rows": 10_000},
            {"feature": "trade_limits", "symbols": 1_000, "rows": 10_000},
        ], 1_000)
        self.assertFalse(result["decision_ready"])
        self.assertEqual(result["blockers"], ["daily_basic"])

    def test_cross_section_sql_requires_eighty_percent_of_the_live_universe(self):
        source = Path("app/research_capacity.py").read_text(encoding="utf-8")
        self.assertIn("greatest(ceil(universe.symbols*0.8)::int,1000)", source)
        self.assertNotIn("least(universe.symbols*0.8,1000)", source)

    def test_historical_coverage_uses_atomic_owner_cold_relations(self):
        class Result:
            def fetchone(self): return {}

        class Connection:
            cursor = object()
            def __init__(self): self.calls = []
            def execute(self, sql):
                self.calls.append(str(sql))
                return Result()

        connection = Connection()
        cold = {f"{name}_cold" for name in TIERED_EVIDENCE_TABLES}
        with patch("app.research_capacity.eligible_cold_tables", return_value=cold):
            current_data_coverage(connection)
        sql = connection.calls[0]
        for relation in ("canonical_bars_daily", "daily_fundamentals", "daily_trade_limits"):
            self.assertIn(f"quant.{relation}_cold", sql)


if __name__ == "__main__":
    unittest.main()
