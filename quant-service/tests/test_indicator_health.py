"""Session indicator health: each check would have caught a real fault."""

from __future__ import annotations

import unittest
from datetime import date, datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from app.indicator_health import indicator_health, indicator_status
from app.indicator_registry import INDICATORS, registry

CN = ZoneInfo("Asia/Shanghai")
DAY = date(2026, 10, 9)


def at(hour, minute, second=0):
    return datetime(2026, 10, 9, hour, minute, second, tzinfo=CN)


class _Connection:
    def __init__(self, *, radar=None, point=None, flows=(), review=(), pools=(), counts=None):
        self.radar, self.point, self.flows = radar, point, list(flows)
        self.review, self.pools, self.counts = list(review), list(pools), counts or {}

    def execute(self, sql, params=()):
        rows = self._rows(sql, params)
        return SimpleNamespace(fetchall=lambda: rows, fetchone=lambda: rows[0] if rows else None)

    def _rows(self, sql, params):
        if "WITH points AS" in sql:
            return [self.radar] if self.radar else [{"points": 0, "last_at": None, "max_gap_seconds": None}]
        if "symbol IS NULL" in sql and "LIMIT 1" in sql:
            return [{"normalized": self.point}] if self.point else []
        if "intraday_board_flow_snapshots" in sql:
            return self.flows
        if "capability=ANY(%s)" in sql:
            return self.review if params[0] == ["longhu:longhu_market_wide:GetPlateInfo_w38"] else self.pools
        for needle, value in self.counts.items():
            if needle in sql:
                return [{"n": value, "day": value}]
        return []


def flow(main_net, unit="cny", taxonomy="longhu_ths_industry", boards=104, minute=at(10, 30)):
    return {"snapshot_minute": minute, "observed_at": minute, "status": "completed", "payload": {
        "providers": {"industry": "longhuvip"},
        "items": [{"taxonomy_key": taxonomy, "net_inflow": main_net / boards, "unit": unit}] * boards}}


POINT = {"pool": {"count": 5100, "turnover": 8e11}, "bands": {"2": {}, "limit": {"now_up": {"count": 43}}}}


class RegistryTests(unittest.TestCase):
    def test_every_indicator_has_a_check_and_a_route(self):
        from app.indicator_health import CHECKS, DERIVED
        self.assertEqual({item.key for item in INDICATORS}, set(CHECKS) | set(DERIVED))
        self.assertTrue(all(item["read_api"].startswith("/api/v1/") and item["live_effect"] == "none" for item in registry()))


class RadarHealthTests(unittest.TestCase):
    def test_a_fresh_continuous_full_radar_is_eligible(self):
        connection = _Connection(radar={"points": 60, "last_at": at(10, 30), "max_gap_seconds": 61}, point=POINT)
        status = indicator_status(connection, "market.radar", DAY, at(10, 31))
        self.assertEqual(status["status"], "ok")
        self.assertTrue(status["decision_eligible"])

    def test_a_gap_inside_continuous_trading_withholds_eligibility(self):
        connection = _Connection(radar={"points": 60, "last_at": at(10, 30), "max_gap_seconds": 900}, point=POINT)
        status = indicator_status(connection, "market.radar", DAY, at(10, 31))
        self.assertEqual((status["status"], status["decision_eligible"]), ("fail", False))

    def test_a_stale_radar_during_the_session_is_not_fresh(self):
        connection = _Connection(radar={"points": 60, "last_at": at(10, 0), "max_gap_seconds": 61}, point=POINT)
        checks = {check["name"]: check for check in indicator_status(connection, "market.radar", DAY, at(10, 31))["checks"]}
        self.assertEqual(checks["fresh"]["status"], "fail")

    def test_a_single_point_after_a_restart_warns_instead_of_missing(self):
        # 2026-10-09 13:14: the first point after the release; continuity cannot be judged yet.
        connection = _Connection(radar={"points": 1, "last_at": at(13, 14), "max_gap_seconds": None}, point=POINT)
        status = indicator_status(connection, "market.radar", DAY, at(13, 15))
        checks = {check["name"]: check["status"] for check in status["checks"]}
        self.assertEqual((checks["continuity"], status["status"]), ("warn", "warn"))
        self.assertFalse(status["decision_eligible"])

    def test_before_the_match_the_radar_is_pending_not_missing(self):
        self.assertEqual(indicator_status(_Connection(), "market.radar", DAY, at(9, 20))["status"], "pending")


class LonghuMoodCrossCheckTests(unittest.TestCase):
    def _connection(self, mood, *, observed=at(15, 0)):
        point = {**POINT, "observed_at": observed.isoformat(), "breadth": {"up": 1080, "down": 4300, "flat": 120},
                 "bands": {"2": {}, "limit": {"now_up": {"count": 43}, "now_down": {"count": 13}}}}
        connection = _Connection(radar={"points": 240, "last_at": observed, "max_gap_seconds": 61}, point=point)
        original = connection._rows

        def rows(sql, params):
            if "MoodNumCount" in str(params):
                return [{"normalized": {"payload": {"list": mood}}}] if mood else []
            return original(sql, params)
        connection._rows = rows
        return connection

    def test_the_closing_counts_agree_with_longhu_within_a_few_percent(self):
        mood = {"SZJS": 1075, "XDJS": 4369, "ZTJS": 43, "DTJS": 15}
        checks = {c["name"]: c for c in indicator_status(self._connection(mood), "market.radar", DAY, at(18, 0))["checks"]}
        self.assertEqual([checks[name]["status"] for name in
                          ("rising_vs_longhu", "falling_vs_longhu", "limit_up_vs_longhu", "limit_down_vs_longhu")],
                         ["ok", "ok", "ok", "ok"])

    def test_a_large_disagreement_fails(self):
        mood = {"SZJS": 2500, "XDJS": 2900, "ZTJS": 90, "DTJS": 13}
        checks = {c["name"]: c for c in indicator_status(self._connection(mood), "market.radar", DAY, at(18, 0))["checks"]}
        self.assertEqual((checks["rising_vs_longhu"]["status"], checks["limit_up_vs_longhu"]["status"]), ("fail", "fail"))

    def test_an_intraday_point_is_not_compared_with_the_closing_tally(self):
        mood = {"SZJS": 2500, "XDJS": 2900, "ZTJS": 90, "DTJS": 13}
        status = indicator_status(self._connection(mood, observed=at(10, 30)), "market.radar", DAY, at(10, 31))
        self.assertFalse(any(check["name"].endswith("_vs_longhu") for check in status["checks"]))


class MainNetHealthTests(unittest.TestCase):
    def test_a_unit_error_is_implausible(self):
        # A Longhu industry row read as 亿 and scaled again: 1e8 times too large.
        connection = _Connection(point=POINT, flows=[flow(-1.6e10 * 1e8, unit="cny")])
        checks = {check["name"]: check for check in indicator_status(connection, "market.main_net", DAY, at(10, 31))["checks"]}
        self.assertEqual(checks["plausible"]["status"], "fail")

    def test_a_sound_main_net_from_enough_boards_is_eligible(self):
        connection = _Connection(point=POINT, flows=[flow(-1.6e10)])
        self.assertEqual(indicator_status(connection, "market.main_net", DAY, at(10, 31))["status"], "ok")

    def test_the_gate_is_as_healthy_as_its_inputs(self):
        connection = _Connection(radar={"points": 60, "last_at": at(10, 30), "max_gap_seconds": 61}, point=POINT,
                                 flows=[flow(-1.6e10)])
        self.assertEqual(indicator_status(connection, "market.direction_gate", DAY, at(10, 31))["status"], "ok")
        self.assertEqual(indicator_status(_Connection(point=POINT), "market.direction_gate", DAY, at(10, 31))["status"], "fail")


class SessionHealthTests(unittest.TestCase):
    def test_post_close_indicators_are_pending_during_the_session(self):
        day = indicator_health(_Connection(), DAY, at(10, 31), ["board.concept_flow", "limits.detail"])
        self.assertEqual([item["status"] for item in day["indicators"]], ["pending", "pending"])

    def test_after_the_close_a_missing_product_is_missing_and_a_thin_one_warns(self):
        later = at(18, 0)
        connection = _Connection(counts={"eastmoney_concept": 120})
        self.assertEqual(indicator_status(connection, "board.concept_flow", DAY, later)["status"], "warn")
        self.assertEqual(indicator_status(_Connection(), "board.industry_flow", DAY, later)["status"], "missing")

    def test_unknown_keys_are_reported(self):
        day = indicator_health(_Connection(), DAY, at(10, 31), ["market.radar", "nope"])
        self.assertEqual(day["unknown_keys"], ["nope"])
        self.assertEqual(len(day["indicators"]), 1)


class LimitDetailHealthTests(unittest.TestCase):
    def test_without_xuangubao_the_cross_check_warns(self):
        import json
        from pathlib import Path
        review = json.loads((Path(__file__).parent / "fixtures" / "longhu_limit_review_20261008.json").read_text(encoding="utf-8"))
        rows = [{"capability": "longhu:longhu_market_wide:GetPlateInfo_w38",
                 "normalized": {"exchange_date": "2026-10-08", "source_date": "2026-10-08", "payload": plate}}
                for plate in review["payload"]["list"]]
        status = indicator_status(_Connection(review=rows), "limits.detail", date(2026, 10, 8),
                                  datetime(2026, 10, 8, 18, 0, tzinfo=CN))
        checks = {check["name"]: check for check in status["checks"]}
        self.assertEqual((checks["present"]["status"], checks["cross_source"]["status"]), ("ok", "warn"))
        self.assertFalse(status["decision_eligible"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
