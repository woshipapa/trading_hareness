"""The data-source and strategy boards: catalog semantics joined to provider health."""

from __future__ import annotations

import unittest
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from app.research_boards import datasource_board, source_verdict, strategy_board

CN = ZoneInfo("Asia/Shanghai")
IN_SESSION = datetime(2026, 10, 9, 10, 30, tzinfo=CN)       # a Friday, continuous trading
EVENING = datetime(2026, 10, 9, 20, 0, tzinfo=CN)


def health(provider, capability, *, ok_ago=None, failures=0, circuit_for=None, now=IN_SESSION):
    return {"provider_key": provider, "capability": capability, "market": "CN", "consecutive_failures": failures,
            "last_success_at": now - ok_ago if ok_ago is not None else None,
            "last_failure_at": now - timedelta(minutes=1) if failures else None, "last_error": "boom" if failures else "",
            "last_latency_ms": 120, "last_row_count": 10,
            "circuit_open_until": now + circuit_for if circuit_for else None}


class _Connection:
    def __init__(self, rows, ledger=()):
        self.rows, self.ledger = rows, list(ledger)

    def execute(self, sql, params=()):
        rows = self.ledger if "strategy_daily_candidates" in sql else self.rows
        return SimpleNamespace(fetchall=lambda: rows)


class VerdictTests(unittest.TestCase):
    def test_each_verdict(self):
        cases = [
            ("retired", set(), [], "retired"),
            ("dormant", set(), [], "dormant"),
            ("active", {"daily"}, [], "unmonitored"),
            ("active", {"intraday"}, [health("x", "q", ok_ago=timedelta(minutes=1), circuit_for=timedelta(minutes=5))],
             "circuit_open"),
            ("active", {"daily"}, [health("x", "q", ok_ago=timedelta(hours=1), failures=3)], "failing"),
            ("active", {"intraday"}, [health("x", "q", ok_ago=timedelta(minutes=40))], "stale"),
            ("active", {"daily"}, [health("x", "q", ok_ago=timedelta(hours=40))], "healthy"),
            ("active", {"daily"}, [health("x", "q", ok_ago=timedelta(days=5))], "stale"),
            ("active", {"daily"}, [health("x", "q")], "never_succeeded"),
            ("active", {"daily"}, [health("x", "q", ok_ago=timedelta(hours=1), failures=1)], "degraded"),
        ]
        for lifecycle, grains, rows, expected in cases:
            with self.subTest(expected=expected):
                self.assertEqual(source_verdict(lifecycle, grains, rows, IN_SESSION)[0], expected)

    def test_an_intraday_source_is_judged_on_the_daily_horizon_outside_the_session(self):
        rows = [health("x", "q", ok_ago=timedelta(hours=5), now=EVENING)]
        self.assertEqual(source_verdict("active", {"intraday"}, rows, EVENING)[0], "healthy")


class DatasourceBoardTests(unittest.TestCase):
    def setUp(self):
        rows = [health("longhuvip", "stock_quote", ok_ago=timedelta(minutes=1)),
                health("tushare_primary", "daily", failures=5, circuit_for=timedelta(minutes=5)),
                health("tencent_free", "order_book_quote", ok_ago=timedelta(days=10), failures=5,
                       circuit_for=timedelta(minutes=3)),
                health("remote_archive", "messages", ok_ago=timedelta(hours=2))]
        self.board = datasource_board(_Connection(rows), IN_SESSION)
        self.sources = {item["key"]: item for item in self.board["sources"]}

    def test_longhu_comes_first_and_retired_sources_last(self):
        order = [item["key"] for item in self.board["sources"]]
        self.assertTrue(all(key.startswith("longhu") for key in order[:2]), order[:3])
        self.assertLess(order.index("longhuvip"), order.index("fuyao_ths"), "Longhu before Fuyao")
        self.assertEqual(self.sources["longhuvip"]["verdict"], "healthy")
        self.assertEqual(self.sources["tushare_primary"]["verdict"], "retired", "a retired source never alarms")
        resting = [item["verdict"] in {"dormant", "retired"} for item in self.board["sources"]]
        self.assertEqual(resting, sorted(resting), "every dormant or retired source comes after the live ones")

    def test_alerts_name_only_live_sources_in_trouble(self):
        alerts = {item["key"]: item["verdict"] for item in self.board["alerts"]}
        self.assertEqual(alerts.get("tencent_free"), "circuit_open")
        self.assertNotIn("tushare_primary", alerts)

    def test_only_a_primary_intraday_source_must_be_fresh_within_minutes(self):
        rows = [health("longhuvip", "stock_quote", ok_ago=timedelta(minutes=40)),
                health("tencent_free", "intraday_minute", ok_ago=timedelta(minutes=40))]
        sources = {item["key"]: item for item in datasource_board(_Connection(rows), IN_SESSION)["sources"]}
        self.assertEqual(sources["longhuvip"]["verdict"], "stale", "the primary watch quote went quiet")
        self.assertEqual(sources["tencent_free"]["verdict"], "healthy", "a fallback minute source is not on a 15-minute clock")

    def test_capabilities_come_from_the_catalog_and_health_from_the_table(self):
        longhu = self.sources["longhuvip"]
        self.assertTrue(any(item["capability"] == "limits.seal_detail" for item in longhu["capabilities"]))
        self.assertEqual(longhu["health"][0]["capability"], "stock_quote")
        self.assertEqual(self.sources["remote_archive"]["lifecycle"], "uncatalogued")


class StrategyBoardTests(unittest.TestCase):
    def test_a_strategy_whose_required_input_source_fails_is_blocked(self):
        rows = [health("fuyao_ths", "a_share_prices_snapshot", ok_ago=timedelta(minutes=1), failures=4)]
        board = strategy_board(_Connection(rows, ledger=[{"strategy_key": "launch_radar", "as_of_date": date(2026, 10, 8), "n": 7}]),
                               IN_SESSION)
        launch = next(item for item in board["strategies"] if item["key"] == "launch_radar")
        self.assertIn(launch["readiness"], {"blocked", "degraded"})
        self.assertEqual(launch["ledger_lines"]["launch_radar"], {"as_of_date": "2026-10-08", "candidates": 7})
        self.assertEqual(launch["live_effect"], "none")
        snapshot_needs = [item for item in launch["inputs"] if item["capability"] == "quote.all_a_snapshot"]
        if snapshot_needs:
            self.assertEqual(snapshot_needs[0]["source_verdict"], "failing")
            self.assertEqual(launch["readiness"], "blocked")

    def test_a_failure_in_another_capability_of_the_source_does_not_block_this_one(self):
        # 2026-10-09: 腾讯's order-book quote failed while its published limit prices landed fine.
        rows = [health("tencent_free", "order_book_quote", ok_ago=timedelta(minutes=2), failures=5,
                       circuit_for=timedelta(minutes=3)),
                health("longhuvip", "stock_quote", ok_ago=timedelta(minutes=1))]
        board = strategy_board(_Connection(rows), IN_SESSION)
        limits = [item for strategy in board["strategies"] for item in strategy["inputs"]
                  if item["capability"] == "limits.prices"]
        self.assertTrue(limits)
        self.assertTrue(all(item["source_verdict"] != "circuit_open" for item in limits))

    def test_the_primary_is_the_live_verified_source_before_a_declared_one(self):
        from app.research_boards import _primary
        primary, _rest = _primary("bars.daily")
        self.assertEqual((primary.source, primary.status), ("longhuvip_composite", "live_verified"))

    def test_every_registered_strategy_is_on_the_board(self):
        from app.platform.strategy_registry import STRATEGY_CONTRACTS
        board = strategy_board(_Connection([]), IN_SESSION)
        self.assertEqual({item["key"] for item in board["strategies"]}, set(STRATEGY_CONTRACTS))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
