import unittest
from datetime import datetime, timedelta, timezone

from app.realtime_provider_health import project_realtime_source_health
from app.realtime_provider_health_repository import project as project_repository_health

NOW = datetime(2026, 9, 17, 6, 39, 50, tzinfo=timezone.utc)


class RealtimeProviderHealthTests(unittest.TestCase):
    def project(self, sources, *, stamp=NOW, configs=None, active=True, now=NOW, health=None):
        payload = project_realtime_source_health(
            {"observed_at": now, "items": health or []},
            {"observed_at": stamp, "summary": {"watched": 84}, "source_status": sources},
            provider_configs=configs or [], session_active=active,
        )
        return {x["source_key"]: x for x in payload["items"]}

    def test_longhu_full_basket_denominator_and_freshness(self):
        item = self.project({"longhuvip_watch": {"status": "completed", "requested": 84,
            "selected": 24, "received": 24, "max_symbols": 24, "decision_eligible_symbols": 20}})["longhuvip"]
        self.assertEqual(item["state"], "partial")
        self.assertEqual(item["requested"], 84)
        self.assertAlmostEqual(item["coverage_ratio"], 24 / 84, places=4)
        self.assertEqual(item["valid_symbols"], 20)
        self.assertEqual(item["evidence_limit"], 24)
        self.assertFalse(item["decision_eligible"])

    def test_stale_success_not_green_and_close_not_outage(self):
        sources = {"longhuvip_watch": {"status": "completed", "requested": 84, "received": 84}}
        self.assertEqual(self.project(sources, stamp=NOW-timedelta(days=1))["longhuvip"]["state"], "stale")
        self.assertEqual(self.project(sources, active=False, now=NOW+timedelta(hours=4))["longhuvip"]["state"], "standby")
        self.assertEqual(self.project(sources, active=False, stamp=NOW-timedelta(days=16))["longhuvip"]["state"], "stale")

    def test_current_config_and_new_failure_override_scan_success(self):
        sources = {"longhuvip_watch": {"status": "completed", "requested": 84, "received": 84}}
        self.assertEqual(self.project(sources, configs=[{"provider_key": "longhuvip", "configured": False}])["longhuvip"]["state"], "unconfigured")
        item = self.project(sources, health=[{"provider_key": "longhuvip", "capability": "stock_quote",
            "last_failure_at": NOW+timedelta(seconds=1), "last_success_at": NOW}])["longhuvip"]
        self.assertEqual(item["state"], "degraded")

    def test_fuyao_uses_full_market_total_not_watch_count(self):
        item = self.project({"fuyao": {"status": "completed", "rows": 5553, "matched": 84,
            "all_a_snapshot": {"total": 5574, "matched_rows": 5553, "status": "fresh", "age_seconds": 0}}})["fuyao_ths"]
        self.assertEqual((item["requested"], item["received"]), (5574, 5553))
        self.assertEqual(item["state"], "partial")

    def test_minute_sources_never_borrow_each_others_success(self):
        items = self.project({"tencent_minute_context": {
            "primary": {"provider": "longhuvip", "provider_status": "completed", "requested": ["a", "b"], "completed": ["a"]},
            "fallback": {"provider": "tencent_free", "provider_status": "completed", "requested": ["a", "b"], "completed": ["a", "b"]},
        }, "tushare_rt_min": {"requested": [], "items": {}}})
        self.assertEqual(items["longhuvip_minute"]["state"], "partial")
        self.assertEqual(items["tencent_minute"]["state"], "healthy")
        self.assertEqual(items["tushare_super_get_rt_min"]["state"], "not_tested")
        self.assertEqual(items["tushare_super_sdk_rt_min"]["state"], "not_tested")

    def test_empty_unknown_cache_and_unused_fallback_are_not_healthy(self):
        items = self.project({"longhuvip_watch": {"status": "completed", "received": 0},
            "eastmoney_board_flow": {"status": "cached", "age_seconds": 0},
            "tencent_watch": {"status": "completed", "decision_eligible_watch_quote_symbols": 84, "sina_watch_quote_rows": 0}})
        self.assertEqual(items["longhuvip"]["state"], "unavailable")
        self.assertEqual(items["eastmoney_board_flow"]["state"], "unknown")
        self.assertEqual(items["sina_free"]["state"], "not_tested")

    def test_all_confirmed_fast_quote_is_healthy_but_no_counts_is_not(self):
        self.assertEqual(self.project({"tushare_rt_k_fast": {"status_counts": {"confirmed": 84}}})["tushare_super_get"]["state"], "healthy")
        self.assertEqual(self.project({"tushare_rt_k_fast": {"status_counts": {}}})["tushare_super_get"]["state"], "not_tested")

    def test_future_or_missing_evidence_time_fails_closed(self):
        src = {"longhuvip_watch": {"status": "completed", "requested": 84, "received": 84}}
        for stamp in (None, NOW+timedelta(hours=1)):
            self.assertEqual(self.project(src, stamp=stamp)["longhuvip"]["state"], "unknown")

    def test_repository_projection_keeps_provider_history_separate(self):
        snapshot = {"observed_at": NOW, "items": [{"provider_key": "longhuvip", "capability": "stock_quote"}]}
        payload = project_repository_health(
            snapshot,
            {"observed_at": NOW, "summary": {"watched": 1}, "source_status": {
                "longhuvip_watch": {"status": "completed", "requested": 1, "received": 1},
            }},
            {"is_open": True},
            [{"provider_key": "longhuvip", "configured": True}],
            NOW,
        )
        self.assertEqual(payload["provider_history"], snapshot["items"])
        self.assertEqual(payload["items"][0]["state"], "healthy")


if __name__ == "__main__":
    unittest.main()
