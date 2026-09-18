"""Normalizers of the public sources, run on real (trimmed) 2026-09-18 payloads."""

import json
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from app.datasources.http import ashare_symbol, number, unwrap_jsonp
from app.datasources.sources import (
    eastmoney_datacenter, eastmoney_hot_rank, eastmoney_ztb, fuyao_evidence, investor_qa, news_flash, ttfund,
)

FIXTURES = json.loads(Path(__file__).with_name("fixtures").joinpath("public_sources_20260918.json").read_text(encoding="utf-8"))
OBSERVED = datetime(2026, 9, 18, 13, 30, tzinfo=timezone.utc)  # 21:30 Shanghai


class SymbolTests(unittest.TestCase):
    def test_equities_only_with_exchange_checks(self):
        self.assertEqual(ashare_symbol("sz300750"), "300750.SZ")
        self.assertEqual(ashare_symbol("SH601091"), "601091.SH")
        self.assertEqual(ashare_symbol("920819"), "920819.BJ")
        self.assertEqual(ashare_symbol("430017"), "430017.BJ")
        self.assertEqual(ashare_symbol("688981.SH"), "688981.SH")
        self.assertIsNone(ashare_symbol("885431.TI"))       # THS index
        self.assertIsNone(ashare_symbol("000001.SH"))       # SSE composite index
        self.assertIsNone(ashare_symbol("161121"))          # fund
        self.assertIsNone(ashare_symbol("600000", "SZ"))    # exchange hint conflicts
        self.assertIsNone(ashare_symbol("BK0596"))

    def test_number_and_jsonp(self):
        self.assertEqual(number("1,234.5"), 1234.5)
        self.assertIsNone(number("--"))
        self.assertIsNone(number(float("nan")))
        self.assertEqual(unwrap_jsonp('jsonpgz({"a": 1});'), {"a": 1})
        self.assertEqual(unwrap_jsonp('var x = {"b": 2};'), {"b": 2})


class EastmoneyPoolTests(unittest.TestCase):
    def test_limit_up_pool_carries_seal_facts(self):
        rows = eastmoney_ztb.normalize_pool("limit_up", FIXTURES["em_zt"])
        row = rows[0]
        self.assertEqual(row["symbol"], "001376.SZ")
        self.assertEqual(row["price"], 12.66)
        self.assertEqual(row["first_seal_time"], "09:25:00")
        self.assertEqual(row["board_count"], 1)
        self.assertEqual(row["limit_stat_text"], "1天1板")
        self.assertGreater(row["seal_fund"], 0)
        self.assertEqual(row["event_type"], "limit_up_pool")

    def test_every_pool_shape(self):
        previous = eastmoney_ztb.normalize_pool("previous_limit_up", FIXTURES["em_yzt"])[0]
        self.assertEqual(previous["previous_board_count"], 1)
        self.assertEqual(previous["previous_first_seal_time"], "10:21:42")
        strong = eastmoney_ztb.normalize_pool("strong", FIXTURES["em_qs"])[0]
        self.assertEqual(strong["symbol"], "688292.SH")
        self.assertEqual(strong["reason"], "60日新高")
        sub_new = eastmoney_ztb.normalize_pool("sub_new", FIXTURES["em_cx"])[0]
        self.assertIsNone(sub_new["limit_price"])       # 1e9 sentinel: no limit on a listing day
        self.assertEqual(sub_new["ipo_date"], "2026-09-17")
        broken = eastmoney_ztb.normalize_pool("broken", FIXTURES["em_zb"])[0]
        self.assertEqual(broken["open_times"], 1)
        down = eastmoney_ztb.normalize_pool("limit_down", FIXTURES["em_dt_old"])[0]
        self.assertEqual(down["limit_down_days"], 1)
        self.assertEqual(down["last_seal_time"], "15:00:00")

    def test_market_code_vetoes_a_mismatch(self):
        self.assertIsNone(eastmoney_ztb.normalize_pool_item("limit_up", {"c": "600000", "m": 0, "p": 1000}))
        self.assertEqual(eastmoney_ztb.normalize_pool_item("limit_up", {"c": "920268", "m": 0, "p": 1000})["symbol"], "920268.BJ")

    def test_only_pools_fuyao_lacks_become_events(self):
        rows = (eastmoney_ztb.normalize_pool("limit_up", FIXTURES["em_zt"])
                + eastmoney_ztb.normalize_pool("previous_limit_up", FIXTURES["em_yzt"]))
        events = eastmoney_ztb.pool_events(rows, OBSERVED)
        self.assertEqual({event["event_type"] for event in events}, {"previous_limit_pool"})
        self.assertIn("pct_change", events[0]["raw"])   # the review reads this key

    def test_stock_changes_identity_and_summary(self):
        rows = [row for row in (eastmoney_ztb.normalize_stock_change(item)
                                for item in FIXTURES["em_changes"]["data"]["allstock"]) if row]
        self.assertEqual(rows[0]["change_label"], "火箭发射")
        self.assertEqual(rows[0]["time"], "14:55:42")
        events = eastmoney_ztb.stock_change_events(rows, date(2026, 9, 18))
        self.assertTrue(events[0]["event_identity_key"].endswith(":2026-09-18:8201:14:55:42"))
        self.assertEqual(events[0]["published_at"], "2026-09-18T14:55:42+08:00")
        summary = eastmoney_ztb.stock_change_summary(rows, date(2026, 9, 18))
        self.assertEqual(summary["by_type"]["火箭发射"], len(rows))

    def test_board_change(self):
        board = eastmoney_ztb.normalize_board_change(FIXTURES["em_bk_changes"]["data"]["allbk"][0])
        self.assertEqual(board["board_code"], "BK0596")
        self.assertEqual(board["top_symbol"], "601091.SH")
        self.assertEqual(board["change_counts"]["火箭发射"], 1756)


class HotRankTests(unittest.TestCase):
    def test_lists_and_history(self):
        popularity = eastmoney_hot_rank.normalize_rank_list("popularity", FIXTURES["em_hot"])
        self.assertEqual(popularity[0], {"symbol": "601091.SH", "list": "popularity", "rank": 1, "rank_change": 0,
                                         "history_rank_change": 0})
        surge = eastmoney_hot_rank.normalize_rank_list("surge", FIXTURES["em_hot_up"])
        self.assertEqual(surge[0]["surge_rank"], 1)
        self.assertEqual(surge[0]["rank_change"], 4543)
        history = eastmoney_hot_rank.normalize_rank_history("000001.SZ", FIXTURES["em_hot_his"])
        self.assertEqual(history[0]["trade_date"], "2025-09-18")
        self.assertTrue(history[0]["effective_at"].endswith("15:00:00+08:00"))


class DatacenterTests(unittest.TestCase):
    def _rows(self, key):
        return FIXTURES[key]["result"]["data"]

    def test_ingest_clock_not_notice_date(self):
        rows = self._rows("dc_holder_trade")
        # NOTICE_DATE is tomorrow; the ingest clock EITIME is the public time.
        self.assertEqual(rows[0]["NOTICE_DATE"][:10], "2026-09-19")
        clock = eastmoney_datacenter.available_at(rows[0], OBSERVED)
        self.assertEqual(clock.isoformat(), "2026-09-18T21:28:16+08:00")
        late = eastmoney_datacenter.available_at({"EITIME": "2026-09-19 08:00:00"}, OBSERVED)
        self.assertEqual(late, OBSERVED)    # never later than the capture
        self.assertEqual(eastmoney_datacenter.available_at({"NOTICE_DATE": "2026-09-18 00:00:00"}, OBSERVED), OBSERVED)

    def test_every_event_report_has_identity_and_symbol(self):
        for key, fixture in (("restricted_release", "dc_lift"), ("holder_count", "dc_holdernum"),
                             ("holder_trade", "dc_holder_trade"), ("block_trade", "dc_block"),
                             ("earnings_forecast", "dc_forecast"), ("earnings_express", "dc_express"),
                             ("repurchase", "dc_repurchase"), ("ipo_calendar", "dc_ipo")):
            events = eastmoney_datacenter.report_events(key, self._rows(fixture), OBSERVED)
            self.assertTrue(events, key)
            event = events[0]
            self.assertRegex(event["ts_code"], r"^\d{6}\.(SH|SZ|BJ)$")
            self.assertTrue(event["event_identity_key"].startswith("eastmoney_datacenter:"))
            self.assertLessEqual(datetime.fromisoformat(event["published_at"]), OBSERVED)

    def test_observation_reports(self):
        rows = eastmoney_datacenter.report_observations("margin_market", self._rows("dc_margin_market"), OBSERVED)
        self.assertIsNone(rows[0]["ts_code"])
        self.assertTrue(rows[0]["effective_at"].startswith("2026-09-17T15:00:00"))
        self.assertEqual(eastmoney_datacenter.report_events("margin_market", self._rows("dc_margin_market"), OBSERVED), [])


class NewsFlashTests(unittest.TestCase):
    def test_cls_levels_and_symbols(self):
        flashes = news_flash.normalize_cls(FIXTURES["cls_roll"])
        with_stock = next(flash for flash in flashes if flash["symbols"])
        self.assertTrue(all(symbol.endswith((".SH", ".SZ", ".BJ")) for symbol in with_stock["symbols"]))
        self.assertIn("important", {flash["importance"] for flash in flashes})
        events = news_flash.flash_events(with_stock)
        self.assertEqual(events[0]["event_type"], "news_flash")
        self.assertIn(with_stock["flash_id"], events[0]["event_identity_key"])

    def test_jin10_importance_and_vip_skip(self):
        flashes = news_flash.normalize_jin10(FIXTURES["jin10"])
        self.assertEqual({flash["importance"] for flash in flashes}, {"important", "normal"})
        vip = {"data": [{"id": "1", "time": "2026-09-18 21:00:00", "type": 0, "important": 1,
                         "data": {"content": "", "vip_title": "会员专享"}}]}
        self.assertEqual(news_flash.normalize_jin10(vip), [])
        tagged = {"data": [{"id": "2", "time": "2026-09-18 21:00:00", "type": 0,
                            "data": {"content": "<b>【标题】</b>正文"}}]}
        self.assertEqual(news_flash.normalize_jin10(tagged)[0]["title"], "标题")

    def test_eastmoney_and_ths_only_attach_equities(self):
        east = news_flash.normalize_eastmoney(FIXTURES["em_fast"])
        self.assertTrue(east)
        self.assertEqual(news_flash._eastmoney_secid("0.161121"), None)     # LOF fund
        self.assertEqual(news_flash._eastmoney_secid("1.600519"), "600519.SH")
        ths = news_flash.normalize_ths(FIXTURES["ths_news"])
        self.assertTrue(all(not flash["symbols"] for flash in ths if "美股" in flash["title"]))

    def test_observation_clock(self):
        flash = news_flash.normalize_cls(FIXTURES["cls_roll"])[0]
        row = news_flash.flash_observation(flash, OBSERVED)
        self.assertLessEqual(datetime.fromisoformat(row["effective_at"]), OBSERVED)
        self.assertEqual(row["available_at"], OBSERVED.isoformat())
        self.assertIsNone(row["ts_code"])


class InvestorQaTests(unittest.TestCase):
    def test_cninfo_answered_question(self):
        rows = investor_qa.normalize_cninfo(FIXTURES["irm_search"])
        self.assertEqual(rows[0]["symbol"], "300188.SZ")
        self.assertIn("股东总户数", rows[0]["answer"])
        event = investor_qa.qa_events(rows)[0]
        self.assertEqual(event["event_identity_key"], "cninfo_irm:investor_qa:2361402871604994048")
        self.assertEqual(event["published_at"], rows[0]["answered_at"])

    def test_sse_html_and_relative_clock(self):
        rows = investor_qa.normalize_sse(FIXTURES["sse_feeds_html"], OBSERVED)
        self.assertTrue(rows)
        self.assertEqual(rows[0]["symbol"], "688813.SH")
        self.assertEqual(rows[0]["company"], "泰金新能")
        self.assertEqual(datetime.fromisoformat(rows[0]["answered_at"]), OBSERVED - timedelta(hours=3))
        self.assertTrue(rows[0]["asked_at"].startswith("2026-07-06T08:55"))

    def test_resolve_sse_time_forms(self):
        self.assertEqual(investor_qa.resolve_sse_time("5分钟前", OBSERVED), OBSERVED - timedelta(minutes=5))
        self.assertEqual(investor_qa.resolve_sse_time("昨天 14:05", OBSERVED).isoformat(), "2026-09-17T14:05:00+08:00")
        self.assertEqual(investor_qa.resolve_sse_time("09月01日 10:00", OBSERVED).isoformat(), "2026-09-01T10:00:00+08:00")
        self.assertIsNone(investor_qa.resolve_sse_time("未知", OBSERVED))


class FuyaoEvidenceTests(unittest.TestCase):
    def test_rank_anomaly_lhb_benchmark(self):
        ranks = fuyao_evidence.rank_observations("a_share_hot_stock_list", FIXTURES["fy_hot_hour"], OBSERVED)
        self.assertEqual(ranks[0]["ts_code"], "601091.SH")
        self.assertLessEqual(datetime.fromisoformat(ranks[0]["effective_at"]), OBSERVED)
        anomalies = fuyao_evidence.anomaly_events(FIXTURES["fy_anomaly"], OBSERVED)
        self.assertEqual(anomalies[0]["event_type"], "stock_anomaly")
        self.assertTrue(anomalies[0]["raw"]["ai_generated"])
        lhb = fuyao_evidence.dragon_tiger_events(FIXTURES["fy_lhb"], OBSERVED)
        self.assertEqual(lhb[0]["event_identity_key"], "fuyao_ths:lhb_ths:601086.SH:2026-09-17")
        bench = fuyao_evidence.auction_benchmark_events(FIXTURES["fy_bench"], OBSERVED)
        self.assertEqual(bench[0]["raw"]["date"], "2026-09-18")

    def test_valuation_index_catalog(self):
        self.assertEqual(fuyao_evidence.valuation_observations(FIXTURES["fy_val"], OBSERVED)[0]["pe_ttm"], 19.297871)
        index = fuyao_evidence.index_quote_observations(FIXTURES["fy_index_snap"], OBSERVED, {"885431.TI": "新能源汽车"})
        self.assertEqual(index[0]["index_name"], "新能源汽车")
        self.assertIsNone(index[0]["ts_code"])
        self.assertIn("885431.TI", fuyao_evidence.index_catalog(FIXTURES["fy_index_all"]))
        self.assertTrue(all(symbol.endswith((".SH", ".SZ", ".BJ"))
                            for symbol in fuyao_evidence.constituent_symbols(FIXTURES["fy_constituents"])))


class FuyaoRequestShapeTests(unittest.IsolatedAsyncioTestCase):
    async def test_pools_walk_every_page(self):
        calls = []

        async def fetch(capability, params):
            calls.append(params)
            page = params["page"]
            return {"item": [{"thscode": f"00000{page}.SZ"}], "pagination": {"pages": 3, "page": page}}

        merged = await fuyao_evidence.fetch_all_pool_pages(fetch, "a_share_limit_up_pool")
        self.assertEqual(len(merged["item"]), 3)
        self.assertEqual([call["page"] for call in calls], [1, 2, 3])
        self.assertTrue(all(call["size"] == 200 for call in calls))

    async def test_code_batches_cap_at_100_and_drop_rejected_codes(self):
        symbols = [f"{600000 + index}.SH" for index in range(150)] + ["000001.SH", "885431.TI"]
        seen = []

        async def fetch(capability, params):
            codes = params["thscodes"].split(",")
            seen.append(len(codes))
            if "600003.SH" in codes:
                raise RuntimeError("Unknown thscode: 600003.SH")
            return {"item": [{"thscode": code} for code in codes]}

        batches, dropped, failures = await fuyao_evidence.fetch_code_batches(fetch, "a_share_auction_snapshot", symbols)
        self.assertEqual(dropped, ["600003.SH"])
        self.assertEqual(failures, [])
        self.assertTrue(all(size <= 100 for size in seen))
        self.assertEqual(sum(len(codes) for codes, _data in batches), 149)   # indices filtered, one dropped


class FundNavTests(unittest.TestCase):
    def test_nav_rows(self):
        rows = ttfund.normalize_nav("161725", FIXTURES["ttfund_lsjz"])
        self.assertEqual(rows[0]["nav_date"], "2026-09-18")
        self.assertEqual(rows[0]["unit_nav"], 0.5283)
        observation = ttfund.nav_observations(rows, OBSERVED)[0]
        self.assertTrue(observation["effective_at"].startswith("2026-09-18T15:00:00"))


if __name__ == "__main__":
    unittest.main()
