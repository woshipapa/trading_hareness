"""Consumers moved off the frozen Tushare-era 同花顺 taxonomies."""

from __future__ import annotations

import unittest
from datetime import date
from types import SimpleNamespace

from app.analyst_expert_research import _basket_symbols, seed_exact_theme_aliases
from app.market_flow_read_model import sector_daily_source
from app.sector_flow_repository import chained_levels


class _Recorder:
    def __init__(self, existing: set[tuple[str, str]] | None = None):
        self.existing, self.calls, self.inserted = existing or set(), [], []

    def execute(self, sql, params=()):
        self.calls.append((sql, params))
        if sql.startswith("SELECT 1 FROM quant.sectors"):
            return SimpleNamespace(fetchone=lambda: {"?column?": 1} if tuple(params) in self.existing else None)
        if "INSERT INTO quant.analyst_theme_board_aliases" in sql:
            self.inserted.append((params[0], params[2], params[3]))
            return SimpleNamespace(fetchone=lambda: {"theme_key": params[0]})
        return SimpleNamespace(fetchall=lambda: [], fetchone=lambda: None)


class AnalystAliasTests(unittest.TestCase):
    def test_each_reviewed_alias_is_also_seeded_under_the_fuyao_list_with_the_same_code(self):
        connection = _Recorder({("fuyao_ths_concept", "886108.TI"), ("ths_concept_flow", "886108.TI"),
                                ("fuyao_ths_industry", "700338.TI")})
        seed_exact_theme_aliases(connection)
        self.assertIn(("remote:ai应用", "fuyao_ths_concept", "886108.TI"), connection.inserted)
        self.assertIn(("remote:ai应用", "ths_concept_flow", "886108.TI"), connection.inserted)
        self.assertIn(("remote:硬件科技", "fuyao_ths_industry", "700338.TI"), connection.inserted)

    def test_a_theme_basket_prefers_the_live_membership_when_it_exists(self):
        connection = _Recorder()
        _basket_symbols(connection, {"scope": "theme", "subject_key": "remote:ai应用", "opinion_date": date(2026, 10, 9),
                                     "available_at": date(2026, 10, 9)})
        sql = connection.calls[-1][0]
        self.assertIn("taxonomy_key LIKE 'fuyao_ths_%%'", sql)
        self.assertIn("NOT EXISTS (SELECT 1 FROM members live", sql)


class ChainedLevelTests(unittest.TestCase):
    def test_returns_chain_only_across_consecutive_capture_sessions(self):
        day = lambda n: date(2026, 10, n)  # noqa: E731
        rows = [{"trading_date": day(8), "sector_key": "A", "change_pct": 1.0, "available_at": 1},
                {"trading_date": day(9), "sector_key": "A", "change_pct": 2.0, "available_at": 2},
                {"trading_date": day(12), "sector_key": "B", "change_pct": 5.0, "available_at": 3},
                {"trading_date": day(13), "sector_key": "A", "change_pct": -1.0, "available_at": 4}]
        series = chained_levels(rows)["A"]
        self.assertAlmostEqual(series[1]["close"] / series[0]["close"] - 1, 0.02)
        self.assertIsNone(series[2]["close"], "A lacks 10-12, which the capture has")
        self.assertNotEqual(series[3]["segment"], series[1]["segment"], "the chain restarts after the gap")


class MarketFlowSourceTests(unittest.TestCase):
    def test_the_served_concept_flow_is_named(self):
        self.assertEqual(sector_daily_source([{"taxonomy_key": "eastmoney_concept"}])["source"], "ths_10jqka_via_akshare")
        self.assertEqual(sector_daily_source([{"taxonomy_key": "ths_concept_flow"}])["sector_key"], "ths_code")
        self.assertEqual(sector_daily_source([])["status"], "missing")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
