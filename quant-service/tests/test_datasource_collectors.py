"""Cadence, de-duplication and archive windows of the data-source collectors."""

import unittest
from datetime import date, datetime, timezone
from unittest.mock import patch

from app.datasources.collectors import intraday, post_close
from app.datasources.derived.tick_flow import Tick

SESSION = datetime(2026, 9, 18, 2, 0, tzinfo=timezone.utc)        # 10:00 Shanghai, Friday
NIGHT = datetime(2026, 9, 18, 18, 30, tzinfo=timezone.utc)        # 02:30 Shanghai, Saturday
EVENING = datetime(2026, 9, 18, 11, 5, tzinfo=timezone.utc)       # 19:05 Shanghai


class Recorder:
    def __init__(self):
        self.events, self.observations, self.health = [], [], []

    async def persist_events(self, provider, rows):
        self.events.append((provider, rows))
        return len(rows)

    async def persist_observations(self, provider, capability, rows):
        self.observations.append((provider, capability, rows))
        return len(rows)

    async def record_health(self, provider, capability, ok, rows, latency_ms, error):
        self.health.append((provider, capability, ok))

    def deps(self, **kwargs):
        return intraday.CollectorDeps(persist_events=self.persist_events, persist_observations=self.persist_observations,
                                      record_health=self.record_health, log=lambda _message: None, **kwargs)


class CadenceTests(unittest.TestCase):
    def test_windows(self):
        self.assertTrue(intraday.in_window("session", SESSION, session_open=True))
        self.assertFalse(intraday.in_window("session", SESSION, session_open=False))
        self.assertFalse(intraday.in_window("day", NIGHT, session_open=False))
        self.assertTrue(intraday.in_window("always", NIGHT, session_open=False))

    def test_news_slows_down_at_night(self):
        state = intraday.CollectorState()
        cadence = intraday.CADENCES["news_flash"]
        state.last_run["news_flash"] = 1000.0
        self.assertTrue(intraday.due(state, cadence, SESSION, session_open=True, monotonic=1100.0))
        self.assertFalse(intraday.due(state, cadence, NIGHT, session_open=False, monotonic=1100.0))
        self.assertTrue(intraday.due(state, cadence, NIGHT, session_open=False, monotonic=1700.0))


class NewsCaptureTests(unittest.IsolatedAsyncioTestCase):
    async def test_seen_flashes_cost_no_second_write_and_health_is_throttled(self):
        recorder = Recorder()
        flashes = [{"provider": "cls_telegraph", "flash_id": "1", "published_at": SESSION.isoformat(), "title": "t",
                    "content": "c", "importance": "important", "symbols": ["600000.SH"], "tags": [], "url": None,
                    "upstream_site": "www.cls.cn"}]

        async def fetch():
            return flashes

        deps = recorder.deps(fetch_flashes={"cls_telegraph": fetch})
        state = intraday.CollectorState()
        first = await intraday.capture_news(deps, state, SESSION)
        second = await intraday.capture_news(deps, state, SESSION)
        self.assertEqual(first["cls_telegraph"]["new"], 1)
        self.assertEqual(second["cls_telegraph"]["new"], 0)
        self.assertEqual(len(recorder.observations), 1)
        self.assertEqual(recorder.events[0][1][0]["event_type"], "news_flash")
        self.assertEqual(len(recorder.health), 1)          # unchanged state within ten minutes

    async def test_one_failing_site_does_not_stop_the_others(self):
        recorder = Recorder()

        async def broken():
            raise RuntimeError("blocked")

        async def empty():
            return []

        deps = recorder.deps(fetch_flashes={"jin10_flash": broken, "ths_flash": empty})
        result = await intraday.capture_news(deps, intraday.CollectorState(), SESSION)
        self.assertEqual(result["jin10_flash"]["status"], "failed")
        self.assertEqual(result["ths_flash"]["status"], "completed")
        self.assertIn(("jin10_flash", "news_flash", False), recorder.health)

    async def test_run_once_respects_switches_and_windows(self):
        recorder = Recorder()
        deps = recorder.deps(fetch_flashes={})
        results = await intraday.run_once(deps, intraday.CollectorState(), NIGHT, session_open=False,
                                          enabled={"investor_qa": False})
        self.assertEqual(set(results), {"news_flash"})    # tape/ranks need a session; Q&A switched off


class SentimentBuildTests(unittest.IsolatedAsyncioTestCase):
    async def test_sentiment_uses_fuyao_pools_tape_and_eastmoney_previous_pool(self):
        recorder = Recorder()

        async def fuyao(route, params):
            if route == "a_share_limit_up_pool":
                return {"item": [{"thscode": "600000.SH", "continue_day_cnt": 2}], "pagination": {"pages": 1}}
            if route == "ths_index_list":
                return {"item": [{"thscode": "885431.TI", "name": "新能源汽车"}]}
            if route == "ths_index_prices_snapshot":
                return {"item": [{"thscode": "885431.TI", "price_change_ratio_pct": 1.5}]}
            return {"item": [], "pagination": {"pages": 1}}

        async def snapshot():
            return [{"symbol": "600000.SH", "pct_change": 10.0, "turnover": 2e8}], {}

        async def previous_turnover(_day):
            return 1e8

        async def previous_pool(pool, trade_date):
            self.assertEqual(pool, "previous_limit_up")
            return [{"symbol": "600000.SH", "previous_board_count": 1}]

        deps = recorder.deps(fuyao_fetch=fuyao, fuyao_snapshot=snapshot, previous_turnover_total=previous_turnover)
        with patch("app.datasources.sources.eastmoney_ztb.fetch_pool", previous_pool):
            result = await intraday.capture_sentiment(deps, intraday.CollectorState(), SESSION)
        self.assertEqual(result["status"], "completed")
        reading = recorder.observations[0][2][0]
        self.assertEqual(reading["promotion"]["layers"]["1to2"]["rate"], 1.0)
        self.assertEqual(reading["turnover"]["change_pct"], 100.0)
        self.assertEqual(reading["concept_strength"][0]["index_name"], "新能源汽车")
        self.assertEqual(reading["sources"]["previous_limit_up"], "eastmoney_ztb")

    async def test_sentiment_without_fuyao_fails_closed(self):
        recorder = Recorder()
        result = await intraday.capture_sentiment(recorder.deps(), intraday.CollectorState(), SESSION)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(recorder.observations, [])


class StockChangeRequestTests(unittest.IsolatedAsyncioTestCase):
    async def test_capture_uses_capability_resolver_when_wired(self):
        from app.datasources.resolver import CapabilityResolver

        recorder = Recorder()
        resolver = CapabilityResolver()
        calls = []

        async def resolved_rows(**_params):
            calls.append(True)
            return [{"symbol": "600000.SH", "name": "x", "time": "09:30:00", "change_type": 8201,
                     "change_label": "火箭发射", "direction": "up", "info": "", "info_values": []}]

        resolver.bind("eastmoney_ztb", "limits.anomaly_tape", resolved_rows)
        result = await intraday.capture_stock_changes(
            recorder.deps(resolver=resolver), intraday.CollectorState(), SESSION,
        )

        self.assertEqual(result["status"], "completed")
        self.assertEqual(calls, [True])
        self.assertEqual(recorder.events[0][0], "eastmoney_ztb")

    async def test_one_request_per_change_type(self):
        from app.datasources.sources import eastmoney_ztb
        requested = []

        async def fake(method, url, *, params=None, **_kwargs):
            requested.append(params["type"])
            return {"data": {"allstock": [{"tm": 93000, "c": "600000", "m": 1, "n": "x", "t": int(params["type"]), "i": ""}]}}

        with patch.object(eastmoney_ztb, "request_json", fake):
            rows = await eastmoney_ztb.fetch_stock_changes((8201, 8193, 4))
        self.assertEqual(requested, ["8201", "8193", "4"])
        self.assertEqual({row["change_label"] for row in rows}, {"火箭发射", "大笔买入", "封涨停板"})


class ArchiveTests(unittest.IsolatedAsyncioTestCase):
    def _deps(self, recorder, **kwargs):
        async def watch():
            return ["000001.SZ", "600519.SH"]

        async def previous(_day):
            return date(2026, 9, 17)

        return post_close.ArchiveDeps(collector=recorder.deps(**kwargs), watch_symbols=watch,
                                      previous_trading_day=previous)

    def test_jobs_follow_their_windows_and_retry_spacing(self):
        state = post_close.ArchiveState()
        due = {job.key for job in post_close.due_jobs(state, EVENING, trading_day=True, monotonic=100.0)}
        self.assertIn("tick_flow", due)
        self.assertNotIn("capital_changes", due)          # opens at 19:30
        self.assertNotIn("sentiment_close", due)          # closed at 17:00
        self.assertEqual(post_close.due_jobs(state, EVENING, trading_day=False, monotonic=100.0), [])
        state.last_attempt["tick_flow"] = 100.0
        self.assertNotIn("tick_flow", {job.key for job in post_close.due_jobs(state, EVENING, trading_day=True, monotonic=200.0)})
        state.done["eastmoney_pools"] = "2026-09-18"
        self.assertNotIn("eastmoney_pools", {job.key for job in post_close.due_jobs(state, EVENING, trading_day=True, monotonic=900.0)})

    async def test_dragon_tiger_stores_the_latest_list_but_waits_for_today(self):
        recorder = Recorder()

        async def fuyao(route, params):
            return {"trade_date": "2026-09-17", "stock_items": [{"thscode": "601086.SH", "name": "国芳集团", "net_value": 1}]}

        with self.assertRaises(RuntimeError):
            await post_close.job_fuyao_dragon_tiger(self._deps(recorder, fuyao_fetch=fuyao), post_close.ArchiveState(),
                                                    date(2026, 9, 18), EVENING)
        # the previous session's list is archived, not dropped
        self.assertEqual(recorder.events[0][1][0]["event_identity_key"], "fuyao_ths:lhb_ths:601086.SH:2026-09-17")

    async def test_tick_flow_falls_back_to_tencent(self):
        recorder = Recorder()

        async def tdx_down(_symbol, _day):
            raise OSError("no host")

        async def tencent(_symbol):
            return [Tick("09:25:00", 11.59, 100, 1159, "B"), Tick("09:31:00", 11.6, 1000, 11600, "B")]

        with patch.object(post_close, "fetch_tdx_ticks", tdx_down), patch.object(post_close, "fetch_tencent_ticks", tencent):
            result = await post_close.job_tick_flow(self._deps(recorder), post_close.ArchiveState(), date(2026, 9, 18), EVENING)
        self.assertEqual(result["sources"], {"tdx_public": 0, "tencent_free": 2})
        row = recorder.observations[0][2][0]
        self.assertEqual(row["tick_source"], "tencent_free")
        self.assertEqual(row["opening_auction"]["amount"], 1159)

    async def test_run_due_jobs_marks_success_and_records_failure(self):
        recorder = Recorder()
        deps = self._deps(recorder)
        state = post_close.ArchiveState()

        async def ok(*_args):
            return {"stored": 1}

        async def broken(*_args):
            raise RuntimeError("upstream empty")

        with patch.dict(post_close.RUNNERS, {"tick_flow": ok, "eastmoney_pools": broken}):
            results = await post_close.run_due_jobs(deps, state, EVENING, trading_day=True,
                                                    enabled={job.key: job.key in {"tick_flow", "eastmoney_pools"}
                                                             for job in post_close.JOBS})
        self.assertEqual(results["tick_flow"]["status"], "completed")
        self.assertEqual(state.done["tick_flow"], "2026-09-18")
        self.assertEqual(results["eastmoney_pools"]["status"], "failed")
        self.assertNotIn("eastmoney_pools", state.done)
        self.assertIn(("public_archive", "eastmoney_pools", False), recorder.health)


if __name__ == "__main__":
    unittest.main()
