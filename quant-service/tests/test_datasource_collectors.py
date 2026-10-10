"""Cadence, de-duplication and archive windows of the data-source collectors."""

import unittest
from datetime import date, datetime, timezone
from unittest.mock import AsyncMock, patch

from app.datasources.collectors import intraday, post_close
from app.datasources.derived.tick_flow import Tick

SESSION = datetime(2026, 9, 18, 2, 0, tzinfo=timezone.utc)        # 10:00 Shanghai, Friday
NIGHT = datetime(2026, 9, 18, 18, 30, tzinfo=timezone.utc)        # 02:30 Shanghai, Saturday
EVENING = datetime(2026, 9, 18, 11, 5, tzinfo=timezone.utc)       # 19:05 Shanghai


class Recorder:
    def __init__(self):
        self.events, self.observations, self.health, self.health_details = [], [], [], []

    async def persist_events(self, provider, rows):
        self.events.append((provider, rows))
        return len(rows)

    async def persist_observations(self, provider, capability, rows):
        self.observations.append((provider, capability, rows))
        return len(rows)

    async def record_health(self, provider, capability, ok, rows, latency_ms, error):
        self.health.append((provider, capability, ok))
        self.health_details.append({"provider": provider, "capability": capability, "ok": ok, "rows": rows,
                                    "error": error})

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

    async def test_dragon_tiger_asks_for_the_session_and_stores_an_older_list_while_it_waits(self):
        recorder = Recorder()
        asked = []

        async def fuyao(route, params):
            asked.append(params)
            return {"trade_date": "2026-09-17", "stock_items": [{"thscode": "601086.SH", "name": "国芳集团", "net_value": 1}]}

        with self.assertRaises(post_close.NotYetPublished):
            await post_close.job_fuyao_dragon_tiger(self._deps(recorder, fuyao_fetch=fuyao), post_close.ArchiveState(),
                                                    date(2026, 9, 18), EVENING)
        self.assertEqual(asked, [{"board_type": "all", "date": "2026-09-18"}])
        # the previous session's list is archived, not dropped
        self.assertEqual(recorder.events[0][1][0]["event_identity_key"], "fuyao_ths:lhb_ths:601086.SH:2026-09-17")

    async def test_dragon_tiger_completes_once_the_dated_list_has_rows(self):
        # 2026-10-09: undated, the route still answered with 10-08; dated, it had 76 rows for 10-09.
        recorder = Recorder()

        async def fuyao(route, params):
            return {"trade_date": params["date"], "stock_items": [{"thscode": "601086.SH", "name": "国芳集团", "net_value": 1}]}

        result = await post_close.job_fuyao_dragon_tiger(self._deps(recorder, fuyao_fetch=fuyao), post_close.ArchiveState(),
                                                         date(2026, 10, 9), EVENING)
        self.assertEqual((result["trade_date"], result["stocks"]), ("2026-10-09", 1))

    async def test_valuation_index_drops_an_index_code_the_quote_route_rejects(self):
        recorder = Recorder()

        async def snapshot():
            return [{"symbol": "600519.SH"}], {}

        async def fuyao(route, params):
            if route == "ths_index_list":
                return {"item": [{"thscode": "886001.TI", "name": "A"}, {"thscode": "886113.TI", "name": "B"}]}
            if route == "ths_index_prices_snapshot":
                codes = params["thscodes"].split(",")
                if "886113.TI" in codes:
                    raise RuntimeError("Unknown thscode: 886113.TI")
                return {"item": [{"thscode": code, "last_price": 1.0} for code in codes]}
            return {"item": []}

        result = await post_close.job_fuyao_valuation_index(
            self._deps(recorder, fuyao_fetch=fuyao, fuyao_snapshot=snapshot), post_close.ArchiveState(),
            date(2026, 10, 9), EVENING)
        self.assertEqual(result["dropped_index_codes"], ["886113.TI"] * 4)    # once per index tag
        self.assertEqual(result["index_quotes"], 4)

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
        health = recorder.health_details[-1]
        self.assertEqual((health["capability"], health["ok"], health["rows"]), ("ticks.session", False, 0))
        self.assertEqual(health["error"], "000001.SZ:OSError,600519.SH:OSError", "Tencent covering a symbol is still a TDX failure")

    async def test_tick_flow_and_capital_log_record_tdx_health(self):
        recorder = Recorder()

        async def tdx_ticks(symbol, _day):
            if symbol == "600519.SH":
                raise OSError("no host")
            return [Tick("09:25:00", 11.59, 100, 1159, "B"), Tick("09:31:00", 11.6, 1000, 11600, "B")], "h:7709/login_one"

        async def tencent(_symbol):
            return [Tick("09:31:00", 1500.0, 10, 15000, "B")]

        async def capital(symbol):
            if symbol == "600519.SH":
                raise OSError("no host")
            return [], "h:7709/login_one"

        deps = self._deps(recorder)
        with patch.object(post_close, "fetch_tdx_ticks", tdx_ticks), patch.object(post_close, "fetch_tencent_ticks", tencent), \
                patch.object(post_close, "fetch_tdx_capital_changes", capital):
            await post_close.job_tick_flow(deps, post_close.ArchiveState(), date(2026, 9, 18), EVENING)
            with self.assertRaises(RuntimeError):     # one symbol answered without changes, the other failed: no rows
                await post_close.job_capital_changes(deps, post_close.ArchiveState(), date(2026, 9, 18), EVENING)
        ticks, capital_log = recorder.health_details
        self.assertEqual((ticks["ok"], ticks["rows"], ticks["error"]), (True, 2, "600519.SH:OSError"))
        self.assertEqual((capital_log["capability"], capital_log["ok"], capital_log["error"]),
                         ("fundamentals.capital_changes", True, "600519.SH:OSError"), "TDX answered one of two symbols")

    async def test_tdx_health_is_not_recorded_without_symbols(self):
        recorder = Recorder()

        async def nothing():
            return []

        deps = post_close.ArchiveDeps(collector=recorder.deps(), watch_symbols=nothing,
                                      previous_trading_day=self._deps(recorder).previous_trading_day)
        await post_close.job_tick_flow(deps, post_close.ArchiveState(), date(2026, 9, 18), EVENING)
        await post_close.job_capital_changes(deps, post_close.ArchiveState(), date(2026, 9, 18), EVENING)
        self.assertEqual(recorder.health_details, [], "an empty watchlist is not a TDX failure")

    async def test_valuation_projection_runs_after_raw_persistence_for_requested_date(self):
        recorder = Recorder()
        called = []

        async def snapshot():
            return [{"symbol": "920001.BJ"}], {}

        async def fuyao(route, _params):
            return {"item": [{"thscode": "920001.BJ", "pe_ttm": -4, "pb_mrq": 2}]} if route == "a_share_valuations_snapshot" else {"item": []}

        async def project(day):
            self.assertEqual(recorder.observations[0][1], "a_share_valuations_snapshot")
            called.append(day)
            return {"status": "partial", "inserted": 1}

        deps = self._deps(recorder, fuyao_fetch=fuyao, fuyao_snapshot=snapshot)
        deps.project_valuations = project
        state = post_close.ArchiveState()
        archived = await post_close.job_fuyao_valuation_index(deps, state, date(2026, 10, 9), EVENING)
        self.assertEqual(called, [])
        self.assertEqual(archived['status'], 'completed')
        result = await post_close.job_daily_valuation_projection(deps, state, date(2026, 10, 9), EVENING)
        self.assertEqual(called, [date(2026, 10, 9)])
        self.assertEqual(result["daily_valuation_projection"]["status"], "partial")
        self.assertEqual(result["status"], "pending")

    async def test_pending_archive_projection_is_retried_instead_of_marked_done(self):
        recorder = Recorder()
        state = post_close.ArchiveState()
        deps = self._deps(recorder)
        deps.project_valuations = AsyncMock(side_effect=[{'status':'partial'}, {'status':'completed'}])
        capture = AsyncMock(return_value={'status':'completed', 'stored':10})
        enabled = {job.key: job.key in {'fuyao_valuation_index','daily_valuation_projection'} for job in post_close.JOBS}
        with patch.dict(post_close.RUNNERS, {'fuyao_valuation_index':capture}), patch.object(post_close.time_module, 'monotonic') as clock:
            clock.return_value = 1000
            first = await post_close.run_due_jobs(deps,state,EVENING,trading_day=True,enabled=enabled)
            self.assertEqual(first['daily_valuation_projection']['status'],'pending')
            self.assertIn('fuyao_valuation_index',state.done)
            self.assertNotIn('daily_valuation_projection',state.done)
            clock.return_value = 1601
            second = await post_close.run_due_jobs(deps,state,EVENING,trading_day=True,enabled=enabled)
        self.assertNotIn('fuyao_valuation_index',second)
        self.assertEqual(capture.await_count,1)
        self.assertEqual(deps.project_valuations.await_count,2)
        self.assertIn('daily_valuation_projection',state.done)

    async def test_projection_restarts_from_database_without_any_provider(self):
        recorder = Recorder()
        deps = self._deps(recorder)
        deps.project_valuations = AsyncMock(return_value={'status':'completed'})
        deps.collector.fuyao_fetch = AsyncMock(side_effect=AssertionError('projection must not fetch'))
        result = await post_close.run_due_jobs(deps,post_close.ArchiveState(),EVENING,trading_day=True,
            enabled={job.key:job.key=='daily_valuation_projection' for job in post_close.JOBS})
        self.assertEqual(result['daily_valuation_projection']['status'],'completed')
        deps.collector.fuyao_fetch.assert_not_awaited()

    async def test_projection_job_is_absent_when_default_callback_disabled(self):
        recorder = Recorder()
        result = await post_close.run_due_jobs(self._deps(recorder),post_close.ArchiveState(),EVENING,trading_day=True,
            enabled={job.key:job.key=='daily_valuation_projection' for job in post_close.JOBS})
        self.assertEqual(result,{})
        self.assertEqual(recorder.health,[])

    async def test_run_due_jobs_marks_success_and_records_failure(self):
        recorder = Recorder()
        deps = self._deps(recorder)
        state = post_close.ArchiveState()

        async def ok(*_args):
            return {"stored": 1}

        async def broken(*_args):
            raise RuntimeError("upstream empty")

        async def unpublished(*_args):
            raise post_close.NotYetPublished("THS hot list history for this session is not published yet")

        with patch.dict(post_close.RUNNERS, {"tick_flow": ok, "eastmoney_pools": broken, "fuyao_attention_close": unpublished}):
            results = await post_close.run_due_jobs(deps, state, EVENING, trading_day=True,
                                                    enabled={job.key: job.key in {"tick_flow", "eastmoney_pools",
                                                                                  "fuyao_attention_close"}
                                                             for job in post_close.JOBS})
        self.assertEqual(results["tick_flow"]["status"], "completed")
        self.assertEqual(state.done["tick_flow"], "2026-09-18")
        self.assertEqual(results["eastmoney_pools"]["status"], "failed")
        self.assertNotIn("eastmoney_pools", state.done)
        self.assertIn(("public_archive", "eastmoney_pools", False), recorder.health)
        # not yet published: retried later, but neither done nor a provider failure
        self.assertEqual(results["fuyao_attention_close"]["status"], "pending")
        self.assertNotIn("fuyao_attention_close", state.done)
        self.assertFalse([entry for entry in recorder.health if entry[1] == "fuyao_attention_close"])

    async def test_tdx_tipinfo_uses_end_of_disclosure_day(self):
        recorder = Recorder()
        evidence = [{"market": "1", "code": "600519", "report_period": "20260630", "eps": 1.2,
                     "first_disclosure_date": date(2026, 8, 29)}]
        with patch("app.datasources.collectors.post_close.tdx_protocol.call",
                   AsyncMock(return_value=(evidence, "h:7709/login_one"))):
            result = await post_close.job_tdx_tipinfo(self._deps(recorder), post_close.ArchiveState(),
                                                      date(2026, 9, 18), EVENING)
        self.assertEqual(result["rows"], 1)
        row = recorder.observations[0][2][0]
        self.assertEqual(row["effective_at"], "2026-08-29T23:59:59+08:00")
        self.assertEqual(row["available_at"], EVENING.isoformat())

    async def test_tdx_gpcw_limits_periods_and_splits_dated_rows(self):
        recorder = Recorder()
        deps = self._deps(recorder)

        async def payloads(_provider, capability):
            return [{"filename": "gpcw20260630.zip", "md5": "a" * 32, "size": 10}] if capability == "tdx_gpcw_manifest" else [
                {"code": "600519", "report_period": "20260630", "first_disclosure_date": date(2026, 8, 29)}]

        deps.observation_payloads = payloads
        gpcw_row = {"code": "600519", "report_period": "2026-06-30", "fields": {"基本每股收益": 1.2, "col9": 3.0},
                    "field_units": {"基本每股收益": "yuan/share", "col9": None}}
        with patch("app.datasources.collectors.post_close.tdx_protocol.call",
                   AsyncMock(side_effect=[("gpcw20260630.zip," + "b" * 32 + ",10", "h:7709/login_one"),
                                          ([gpcw_row], "h:7709/login_one")])), \
             patch("app.datasources.collectors.post_close.tdx_fin_history.gpcw",
                   return_value=[gpcw_row]):
            result = await post_close.job_tdx_gpcw(deps, post_close.ArchiveState(), date(2026, 9, 18), EVENING)
        self.assertEqual(result["downloaded"], 1)
        stored = [rows for _provider, capability, rows in recorder.observations if capability == "tdx_gpcw"][0]
        self.assertEqual(stored[0]["availability_basis"], "tipinfo_first_disclosure")
        self.assertEqual(stored[0]["fields"], {"基本每股收益": 1.2})

    async def test_tdx_index_bars_backfill_once_then_request_five(self):
        recorder = Recorder()
        deps = self._deps(recorder)
        requested = []

        async def latest(_provider, capability):
            return {"999999.SH": {}} if capability == "tdx_index_daily_bars" else {}

        async def daily(*, symbol, count):
            requested.append(("daily", symbol, count))
            from app.datasources.contracts import CapabilityEvidence
            return CapabilityEvidence([{"symbol": symbol, "trade_date": "2026-10-09", "close": 1, "open": 1,
                                        "high": 1, "low": 1, "amount": 1, "volume_raw": 1,
                                        "up_count": 2, "down_count": 1}])

        async def breadth(*, symbol, count):
            requested.append(("breadth", symbol, count))
            from app.datasources.contracts import CapabilityEvidence
            return CapabilityEvidence([{"symbol": symbol, "trade_date": "2026-10-09", "up_count": 2, "down_count": 1}])

        deps.latest_observation_payloads = latest
        with patch("app.datasources.collectors.post_close.tdx_bars.fetch_index_daily", daily), \
             patch("app.datasources.collectors.post_close.tdx_bars.fetch_index_breadth", breadth):
            await post_close.job_tdx_index_bars(deps, post_close.ArchiveState(), date(2026, 10, 9), EVENING)
        self.assertEqual(requested[0][2], 5)
        self.assertEqual(requested[2][2], 800)
        self.assertEqual({capability for _provider, capability, _rows in recorder.observations},
                         {"tdx_index_daily_bars", "tdx_index_breadth"})

    async def test_tdx_mac_boards_persists_catalog_and_delta_membership(self):
        recorder = Recorder()
        deps = self._deps(recorder)
        from app.datasources.contracts import CapabilityEvidence
        catalog = CapabilityEvidence([{"board_code": "880710", "name": "Industry", "board_type": 3},
                                      {"board_code": "880001", "name": "Concept", "board_type": 0}])
        calls = []

        async def membership(*, sector_key, board_type):
            calls.append((sector_key, board_type))
            return CapabilityEvidence([{"symbol": "600519.SH", "name": "Moutai"}])

        async def delta(taxonomy, sector, members, observed_at):
            self.assertEqual((taxonomy, sector), ("tdx_mac_type_3", "880710")
                             if sector == "880710" else ("tdx_mac_type_0", "880001"))
            return {"members": len(members), "opened": 1, "closed": 0}

        deps.persist_membership_delta = delta
        with patch("app.datasources.collectors.post_close.tdx_mac.fetch_board_catalog", AsyncMock(return_value=catalog)), \
             patch("app.datasources.collectors.post_close.tdx_mac.fetch_membership", membership):
            result = await post_close.job_tdx_mac_boards(deps, post_close.ArchiveState(), date(2026, 10, 9), EVENING)
        self.assertEqual(result["membership_requests"], 2)
        self.assertEqual(result["opened"], 2)
        self.assertEqual(len(calls), 2)


if __name__ == "__main__":
    unittest.main()
