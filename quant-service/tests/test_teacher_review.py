from __future__ import annotations

import asyncio
import copy
import json
import os
import unittest
from datetime import date, datetime, timedelta, timezone
from functools import partial
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from app import teacher_review_service as service
from app.intraday_alerts import intraday_alert_text
from app.intraday_signal_generation import IntradaySignalGenerationDependencies, generate_intraday_signals
from app.live_policy import live_policy_gate
from app.teacher_review_plan import bullish_divergence, limit_up_price, parse_longhu_kline, plan_stock
from app.teacher_review_playbooks import CATALOG, validate_pack
from app.teacher_review_rules import (PeriodDivergenceBook, SnapshotTape, TeacherMarketBook, count_sector_limit_ups, evaluate,
                                      scan_features, teacher_review_signals)

CN = ZoneInfo("Asia/Shanghai")
FIXTURE = Path(__file__).parent / "fixtures" / "teacher_review_pack_20260921.json"


def load_pack() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def bars(closes: list[float], start: date = date(2026, 6, 1), amount: float = 2e8) -> list[dict]:
    rows, day = [], start
    for close in closes:
        while day.weekday() >= 5:
            day += timedelta(days=1)
        rows.append({"date": day.strftime("%Y%m%d"), "open": close, "close": close, "high": close * 1.01,
                     "low": close * 0.99, "volume_lot": amount / close / 100, "amount": amount,
                     "turnover": 3.0, "limit_up": False})
        day += timedelta(days=1)
    return rows


def at(hour: int, minute: int, day: date = date(2026, 9, 22)) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=CN).astimezone(timezone.utc)


def quote(price: float, pre_close: float, *, amount: float, volume_lot: float, volume_ratio: float = 2.0,
          turnover: float = 5.0, sealed: bool = False, opened: float | None = None, high: float | None = None) -> dict:
    book = {"book_side": "bid_only", "bids": [{"price": price, "size": 50000}], "asks": []} if sealed else {
        "book_side": "two_sided", "bids": [{"price": price - 0.01, "size": 100}], "asks": [{"price": price, "size": 100}]}
    return {
        "price": price, "pct_change": round((price / pre_close - 1) * 100, 3), "amount": amount,
        "volume_ratio": volume_ratio, "turnover_rate": turnover, "price_source": "longhuvip_watch_quote",
        "price_freshness": {"status": "fresh"},
        "raw": {"longhu_watch_quote": {"pre_close": pre_close, "open": opened or pre_close, "high": high or price,
                                       "low": min(price, pre_close), "amount": amount, "volume": volume_lot,
                                       "order_book": book}},
    }


def watch(symbol: str, playbook: str, params: dict, extra: dict | None = None, *, session: str = "2026-09-22",
          name: str = "测试") -> dict:
    return {"symbol": symbol, "alert_on_entry": True, "metadata": {"teacher_review": {
        "status": "active", "session_date": session, "playbook": playbook, "kind": CATALOG[playbook]["kind"],
        "params": params, "extra": extra or {}, "name": name, "pack_id": "abc12345", "analyst_id": "itougu-test",
        "review_date": "2026-09-21", "teacher": "老师原话", "evidence_times": ["14:28-15:01"],
    }}}


class PackValidationTests(unittest.TestCase):
    def test_reviewed_pack_is_valid(self):
        self.assertEqual(validate_pack(load_pack()), [])

    def test_problems_are_reported_per_stock(self):
        pack = load_pack()
        pack["stocks"][0]["params"].pop("auction_amount_min_src")
        pack["stocks"][1]["playbook"] = "free_form_code"
        pack["stocks"][2]["evidence"] = []
        problems = validate_pack(pack)
        self.assertTrue(any("auction_amount_min_src" in item for item in problems))
        self.assertTrue(any("unknown playbook free_form_code" in item for item in problems))
        self.assertTrue(any("evidence" in item for item in problems))


class PlanTests(unittest.TestCase):
    def test_longhu_kline_payload_is_normalized_oldest_first(self):
        rows = parse_longhu_kline([{"x": ["20260921", "20260918"], "y": [[17.82, 18.02, 18.3, 17.52], [17.7, 17.64, 18.21, 17.34]],
                                    "vol": [151963, 204546], "bal": [273812018, 362749528], "turnover": [4.66, 6.27],
                                    "stateZT": [0, 0]}])
        self.assertEqual([row["date"] for row in rows], ["20260918", "20260921"])
        self.assertEqual(rows[-1]["close"], 18.02)

    def test_limit_prices_match_exchange_rounding(self):
        self.assertEqual(limit_up_price(8.29, "600630"), 9.12)
        self.assertEqual(limit_up_price(2.89, "002285"), 3.18)
        self.assertEqual(limit_up_price(45.75, "301282"), 54.90)

    def test_plan_uses_only_supplied_bars_and_prefers_the_higher_short_ma(self):
        closes = [10 + 0.05 * i for i in range(40)] + [12.5, 12.2, 12.0, 11.9, 11.8]
        stock = {"code": "603386", "name": "骏亚科技", "playbook": "ma5_reclaim_or_divergence",
                 "params": {"prior_high": 22.11, "support_level": 11.5, "support_ma": 10, "invalid_below": 11.0}}
        plan = plan_stock(stock, bars(closes))
        self.assertAlmostEqual(plan["extra"]["a_level"], max(plan["levels"]["ma5"], plan["levels"]["ma10"]), places=3)
        self.assertEqual(plan["levels"]["date"], bars(closes)[-1]["date"])

    def test_divergence_requires_a_lower_low_with_a_higher_dif(self):
        closes = [20 - 0.25 * i for i in range(30)] + [12.5 + 0.2 * i for i in range(8)]
        closes += [14.1 - 0.12 * i for i in range(16)] + [12.3 + 0.1 * i for i in range(5)]
        rows = [{"date": str(i), "open": c, "close": c, "high": c + 0.05, "low": c - 0.05} for i, c in enumerate(closes)]
        self.assertTrue(bullish_divergence(rows)["found"])


def divergence_rows(stamp_day: str = "20260921") -> list[dict]:
    """Period K-line rows (data-plane form) that end in a two-leg bullish divergence."""
    closes = [20 - 0.25 * i for i in range(30)] + [12.5 + 0.2 * i for i in range(8)]
    closes += [14.1 - 0.12 * i for i in range(16)] + [12.3 + 0.1 * i for i in range(5)]
    return [{"bar_time": f"{stamp_day[:6]}{int(stamp_day[6:]) - (len(closes) - 1 - i) // 8:02d}{1000 + i % 8:04d}",
             "open": c, "close": c, "high": c + 0.05, "low": c - 0.05} for i, c in enumerate(closes)]


class DivergenceTests(unittest.TestCase):
    def test_too_few_bars_is_not_judgeable_rather_than_no_divergence(self):
        from app.teacher_review_plan import divergence_status, divergence_summary
        status = divergence_status([], None)
        self.assertEqual((status["found"], status["status"], status["bars"]), (False, "insufficient_bars", 0))
        self.assertEqual(divergence_summary({"30": status, "60": status}), (False, False))
        self.assertEqual(divergence_summary({"30": False, "60": True}), (True, False))   # v1 plans

    def test_pre_session_divergence_reads_longhu_history_up_to_the_previous_close(self):
        rows = divergence_rows("20260921") + [{"bar_time": "202609221000", "open": 9.0, "close": 9.0, "high": 9.1, "low": 8.0}]
        requested = []

        async def period_bars(symbol, period):
            requested.append((symbol, period))
            return rows

        async def run_database(action, timeout_seconds=30):
            return action()

        deps = service.TeacherReviewDependencies(
            database=None, run_database=run_database, now_utc=lambda: at(21, 0, date(2026, 9, 21)),
            send_alert=None, max_symbols=lambda: 100, exchange_for=lambda s: s[-2:], period_bars=period_bars)
        result = asyncio.run(service.period_divergence("603386.SH", "30", date(2026, 9, 21), deps))
        self.assertEqual(requested, [("603386.SH", "30")])
        self.assertEqual((result["status"], result["source"], result["found"]), ("ok", "longhuvip_kline", True))
        self.assertEqual(result["bars"], len(rows) - 1)          # the 09-22 bar is after the cut-off
        self.assertTrue(result["through"].startswith("20260921"))

    def test_a_failed_longhu_read_falls_back_to_stored_minutes_and_says_so(self):
        async def period_bars(symbol, period):
            raise RuntimeError("gateway 503")

        async def run_database(action, timeout_seconds=30):
            return action()

        deps = service.TeacherReviewDependencies(
            database=None, run_database=run_database, now_utc=lambda: at(21, 0, date(2026, 9, 21)),
            send_alert=None, max_symbols=lambda: 100, exchange_for=lambda s: s[-2:], period_bars=period_bars)
        with patch.object(service.repo, "minute_period_bars", lambda _db, symbol, *, through, period: []):
            result = asyncio.run(service.period_divergence("603386.SH", "60", date(2026, 9, 21), deps))
        self.assertEqual(result["status"], "insufficient_bars")
        self.assertIn("gateway 503", result["source_error"])

    def test_intraday_divergence_opens_path_b_and_an_unjudgeable_one_stays_unknown(self):
        from app.teacher_review_plan import divergence_status
        from app.teacher_review_service import period_bars_through
        params = {"prior_high": 22.11, "support_level": 11.5, "support_ma": 10, "invalid_below": 11.0}
        plan = watch("603386.SH", "ma5_reclaim_or_divergence", params,
                     {"a_level": 13.0, "zone": [11.3, 11.7], "divergence_found": False, "divergence_judgeable": False,
                      "divergence": {"30": {"found": False, "status": "insufficient_bars", "bars": 0}}})
        in_zone = quote(11.6, 11.8, amount=1e8, volume_lot=86000, volume_ratio=1.0)
        in_zone["raw"]["longhu_watch_quote"]["low"] = 11.5
        watching = teacher_review_signals(plan, in_zone, {"vwap": 11.55, "return_5m_pct": 0.1}, None, at(10, 30))
        self.assertEqual(watching, [])
        book = PeriodDivergenceBook()
        live = {period: divergence_status(period_bars_through(divergence_rows("20260922"), date(2026, 9, 22)), "longhuvip_kline")
                for period in ("30", "60")}
        book.store("603386.SH", at(10, 29), live)
        signals = teacher_review_signals(plan, in_zone, {"vwap": 11.55, "return_5m_pct": 0.1}, None, at(10, 30),
                                         divergence_book=book)
        self.assertEqual(signals[0]["signal_key"], "603386.SH:entry:teacher_review:ma5_reclaim_or_divergence:B")
        self.assertIn("divergence_live", signals[0]["conditions"]["teacher_review"]["features"])
        features = scan_features("603386.SH", in_zone, {"vwap": 11.55}, at(10, 30))
        unknown = [item for item in evaluate(plan["metadata"]["teacher_review"], features)["signals"]
                   if item["name"].startswith("B：30/60")]
        self.assertIsNone(unknown[0]["pass"])

    def test_book_refreshes_at_most_once_a_minute_and_resets_daily(self):
        book = PeriodDivergenceBook()
        self.assertEqual(book.due(["A", "B"], at(9, 31)), ["A", "B"])
        book.store("A", at(9, 31), {"30": {"found": True, "status": "ok"}})
        self.assertEqual(book.due(["A", "B"], at(9, 31, date(2026, 9, 22)) + timedelta(seconds=30)), ["B"])
        self.assertEqual(book.due(["A"], at(9, 32, date(2026, 9, 22)) + timedelta(seconds=1)), ["A"])
        self.assertIsNone(book.get("A", at(9, 31, date(2026, 9, 23))))


class PrecisionTests(unittest.TestCase):
    """v3: today's MA, close-confirmed invalidation, 09:25 auction, sector counts, leader state."""

    def test_today_ma_uses_the_frozen_prefix_and_invalidates_only_at_the_close(self):
        prefix = {"10": 9 * 20.0, "20": 19 * 19.0}           # yesterday's 9 / 19 closes
        plan = watch("002913.SZ", "trend_continuation", {"prior_high": 22.0, "hold_ma": 10, "floor_ma": 20},
                     {"ma_prefix": prefix, "hold_ma": 10, "floor_ma": 20, "hold_level": 99, "floor_level": 99})
        dip = quote(17.0, 20.0, amount=1e8, volume_lot=5e4)
        morning = evaluate(plan["metadata"]["teacher_review"], scan_features("002913.SZ", dip, {"vwap": 17.5}, at(10, 0)))
        self.assertEqual(morning["action"], "watch")          # 17 < MA20 (19.1) but before 14:50: warning only
        self.assertTrue(any(item["name"].startswith("盘中跌破当日MA20") for item in morning["signals"]))
        closing = evaluate(plan["metadata"]["teacher_review"], scan_features("002913.SZ", dip, {"vwap": 17.5}, at(14, 51)))
        self.assertEqual(closing["action"], "invalid")
        hold = next(item for item in morning["signals"] if item["name"].startswith("持有"))
        self.assertIn(str(round((9 * 20.0 + 17.0) / 10, 4)), hold["value"])   # today's MA10, not the frozen 99

    def test_news_bet_needs_the_sector_to_form_and_race_quits_when_it_fades(self):
        book = TeacherMarketBook()
        news = watch("000532.SZ", "relay_news_conditional", {"sector": "大金融", "sector_min_limit_ups": 3})
        strong = quote(15.0, 14.26, amount=2e8, volume_lot=1.4e5, volume_ratio=2.0)
        minute = {"vwap": 14.8, "minute_volume_multiple": 3.0, "return_5m_pct": 0.5}
        self.assertEqual(teacher_review_signals(news, strong, minute, None, at(10, 0), market_book=book), [])
        rows = [{"name": name, "limit_up_reason": reason} for name, reason in
                (("华金资本", "金融+珠海"), ("南华期货", "期货+金融"), ("青岛金王", "数字货币"), ("万科A", "房地产"))]
        book.store_sectors(at(10, 0), at(9, 59), count_sector_limit_ups(rows, ["大金融", "房地产"]))
        entry = teacher_review_signals(news, strong, minute, None, at(10, 0), market_book=book)
        self.assertEqual(entry[0]["signal_type"], "entry")
        race = watch("600606.SH", "relay_race", {"sector": "房地产", "sector_min_limit_ups": 4})
        weak = quote(1.7, 1.6, amount=2e8, volume_lot=1.2e6, volume_ratio=2.0)

        def race_at(hour, minute_, count):
            estate = [{"name": f"地产{i}", "limit_up_reason": "房地产"} for i in range(count)]
            book.store_sectors(at(hour, minute_), at(hour, minute_), count_sector_limit_ups(estate, ["房地产"]))
            features = {**scan_features("600606.SH", weak, minute, at(hour, minute_)),
                        "sector_counts": book.sector_counts(at(hour, minute_)), "sector_peaks": book.sector_peaks(at(hour, minute_))}
            return evaluate(race["metadata"]["teacher_review"], features)["action"]

        self.assertNotEqual(race_at(10, 30, 1), "invalid")     # not formed yet (9/21 had 1 at 10:30, 8 at close)
        self.assertNotEqual(race_at(13, 30, 8), "invalid")     # formed
        self.assertNotEqual(race_at(14, 0, 5), "invalid")      # 5 >= 8/2
        self.assertEqual(race_at(14, 30, 3), "invalid")        # fell below half of today's peak

    def test_a_single_degraded_scan_is_not_reported_as_missing_data(self):
        tape = SnapshotTape()
        plan = watch("001216.SZ", "relay_one_word", {"auction_amount_min": 5e8, "turnover_max_pct": 12.0, "prior_high": 24.06})
        no_book = {"price": 26.47, "pct_change": 10.0, "turnover_rate": 3.0, "price_source": "fuyao_ths_all_a_snapshot",
                   "price_freshness": {"status": "missing_timestamp"}, "raw": {}}
        full = quote(26.47, 24.06, amount=6e8, volume_lot=2.3e5, turnover=3.0, sealed=True, opened=26.47)
        issues = lambda signals: [s for s in signals if s["signal_type"] == "data_issue"]
        start = at(10, 30)
        self.assertEqual(issues(teacher_review_signals(plan, no_book, None, None, start, tape=tape)), [])
        teacher_review_signals(plan, full, {"vwap": 26.47}, None, start + timedelta(seconds=30), tape=tape)   # recovered
        for step in (60, 90):
            self.assertEqual(issues(teacher_review_signals(plan, no_book, None, None, start + timedelta(seconds=step), tape=tape)), [])
        persisted = issues(teacher_review_signals(plan, no_book, None, None, start + timedelta(seconds=120), tape=tape))
        self.assertEqual(len(persisted), 1)                  # 3 consecutive scans over 60 s
        self.assertTrue(persisted[0]["independent_confirmation"])
        self.assertEqual(persisted[0]["conditions"]["teacher_review"]["quote_source"], "fuyao_ths_all_a_snapshot")

    def test_stale_sector_counts_are_unknown_not_zero(self):
        book = TeacherMarketBook()
        book.store_sectors(at(10, 0), at(9, 40), {"大金融": {"count": 4}})
        self.assertEqual(book.sector_counts(at(10, 0)), {})

    def test_follower_reads_the_leaders_own_scan_state(self):
        tape = SnapshotTape()
        leader = watch("601579.SH", "leader_benchmark_pullback", {"pullback_ma": 5, "trend_floor_ma": 10},
                       {"pullback_level": 30.0, "floor_level": 27.0})
        follower = watch("600059.SH", "sympathy_follow", {"leader": "601579", "leader_strong_pct": 5.0, "leader_weak_pct": -3.0,
                                                          "leader_name": "会稽山"}, {"leader_ts_code": "601579.SH"})
        minute = {"vwap": 11.6, "minute_volume_multiple": 3.0, "return_5m_pct": 0.4}
        mine = quote(11.9, 11.48, amount=3e8, volume_lot=2.6e5, volume_ratio=2.0)
        self.assertEqual(teacher_review_signals(follower, mine, minute, None, at(10, 0), tape=tape), [])
        teacher_review_signals(leader, quote(39.7, 36.12, amount=5e8, volume_lot=1.3e5, sealed=True), {"vwap": 38.0}, None,
                               at(10, 0, ), tape=tape)
        signals = teacher_review_signals(follower, mine, minute, None, at(10, 0) + timedelta(seconds=10), tape=tape)
        self.assertEqual(signals[0]["signal_type"], "entry")
        teacher_review_signals(leader, quote(34.9, 36.12, amount=5e8, volume_lot=1.3e5), {"vwap": 35.5}, None,
                               at(10, 1), tape=tape)
        quit_ = teacher_review_signals(follower, mine, minute, None, at(10, 1) + timedelta(seconds=10), tape=tape)
        self.assertTrue(any(item["signal_key"].endswith("teacher_review_invalid:sympathy_follow") for item in quit_))

    def test_one_word_relay_reads_the_0925_auction_snapshot(self):
        book = TeacherMarketBook()
        plan = watch("001216.SZ", "relay_one_word", {"auction_amount_min": 5e8, "turnover_max_pct": 12.0, "prior_high": 24.06})
        sealed = quote(26.47, 24.06, amount=6.2e8, volume_lot=234000, turnover=3.0, sealed=True, opened=26.47)
        book.store_auction("001216.SZ", at(9, 30), {"amount": 4.1e8, "seal_amount": 3.2e8, "source": "fuyao_auction_0925", "final": True})
        signals = teacher_review_signals(plan, sealed, {"vwap": 26.47}, None, at(9, 30), market_book=book)
        self.assertEqual(signals, [])                      # 4.1亿 < 5亿 although the cumulative amount is 6.2亿
        features = scan_features("001216.SZ", sealed, {"vwap": 26.47}, at(9, 30))
        self.assertEqual(features["auction_amount"], 6.2e8)   # the proxy the snapshot replaces

    def test_volume_projection_follows_the_stocks_own_curve(self):
        from app.teacher_review_rules import _session_share
        profile = {"10:00": 0.34, "10:30": 0.48, "11:00": 0.59, "11:30": 0.65, "13:30": 0.75, "14:00": 0.82, "14:30": 0.90, "15:00": 1.0}
        self.assertAlmostEqual(_session_share(profile, "10:00", 30), 0.34)
        self.assertAlmostEqual(_session_share(profile, "10:15", 45), 0.41)
        self.assertAlmostEqual(_session_share(profile, "12:10", 120), 0.65)
        self.assertAlmostEqual(_session_share(None, "10:00", 30), 30 / 240)


class RuleTests(unittest.TestCase):
    def test_one_word_relay_needs_auction_amount_turnover_and_seal(self):
        plan = watch("001216.SZ", "relay_one_word", {"auction_amount_min": 5e8, "turnover_max_pct": 12.0, "prior_high": 24.26})
        sealed = quote(26.47, 24.06, amount=6e8, volume_lot=226700, turnover=3.0, sealed=True, opened=26.47)
        signals = teacher_review_signals(plan, sealed, {"vwap": 26.47}, None, at(9, 30))
        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0]["signal_type"], "entry")
        self.assertEqual(signals[0]["policy_profile"], "teacher_review")
        # The same amount observed after 09:31 is no longer an auction proxy.
        self.assertEqual(teacher_review_signals(plan, sealed, {"vwap": 26.47}, None, at(9, 45)), [])

    def test_acceleration_window_and_failure_amount(self):
        params = {"seal_amount_max": 4e8, "entry_amount_range": [2e8, 3e8], "entry_pct_min": 7.0, "fail_amount": 8.59e8}
        plan = watch("002285.SZ", "relay_acceleration", params)
        in_window = quote(3.12, 2.89 * 1.1, amount=2.5e8, volume_lot=820000, sealed=False)
        in_window["raw"]["longhu_watch_quote"]["pre_close"] = 2.89
        signal = teacher_review_signals(plan, in_window, {"vwap": 3.05}, None, at(9, 50))
        self.assertEqual(signal[0]["signal_type"], "entry")
        failed = quote(3.0, 2.89, amount=9e8, volume_lot=3000000, sealed=False)
        signal = teacher_review_signals(plan, failed, {"vwap": 3.02}, None, at(11, 0))
        self.assertIn("teacher_review_invalid", signal[0]["signal_key"])
        self.assertTrue(signal[0]["independent_confirmation"])

    def test_plan_is_inert_outside_its_session_and_for_record_only_playbooks(self):
        plan = watch("600630.SH", "relay_race", {}, session="2026-09-23")
        self.assertEqual(teacher_review_signals(plan, quote(9.5, 9.12, amount=1e8, volume_lot=1e5), {}, None, at(10, 0)), [])
        rejected = watch("600127.SH", "rejected", {})
        self.assertEqual(teacher_review_signals(rejected, quote(15.0, 15.75, amount=1e8, volume_lot=1e5), {}, None, at(10, 0)), [])

    def test_breakout_needs_volume_and_vwap(self):
        params = {"prior_high": 33.56, "floor_ma": 10}
        plan = watch("001368.SZ", "prior_high_breakout", params, {"floor_level": 29.3})
        weak = quote(33.8, 33.3, amount=2e8, volume_lot=59000, volume_ratio=0.9)
        self.assertEqual(teacher_review_signals(plan, weak, {"vwap": 33.5, "return_5m_pct": 0.1}, None, at(10, 5)), [])
        strong = quote(33.8, 33.3, amount=2e8, volume_lot=59000, volume_ratio=1.8)
        signal = teacher_review_signals(plan, strong, {"vwap": 33.5, "return_5m_pct": 0.2}, None, at(10, 5))
        self.assertEqual(signal[0]["signal_key"], "001368.SZ:entry:teacher_review:prior_high_breakout")

    def test_platform_breakout_waits_for_a_platform(self):
        extra = {"platform_upper": 20.58, "platform_lower": 19.3, "ma10": 15.7, "days_since_peak": 0}
        strong = quote(21.0, 20.43, amount=2e9, volume_lot=1e6, volume_ratio=2.0)
        minute = {"vwap": 20.8, "return_5m_pct": 0.5}
        self.assertEqual(teacher_review_signals(watch("000993.SZ", "platform_breakout", {}, extra), strong, minute, None, at(10, 0)), [])
        extra["days_since_peak"] = 2
        self.assertEqual(len(teacher_review_signals(watch("000993.SZ", "platform_breakout", {}, extra), strong, minute, None, at(10, 0))), 1)

    def test_sympathy_leader_is_matched_by_ts_code(self):
        plan = plan_stock({"code": "600059", "name": "古越龙山", "playbook": "sympathy_follow",
                           "params": {"leader": "601579", "leader_strong_pct": 5.0, "leader_weak_pct": -3.0}},
                          bars([10 + 0.02 * i for i in range(30)]))
        self.assertEqual(plan["extra"]["leader_ts_code"], "601579.SH")

    def test_seal_needs_a_book_and_a_missing_book_is_reported(self):
        no_book = {**quote(10.03, 9.12, amount=1e8, volume_lot=1e5), "raw": {"longhu_watch_quote": {"pre_close": 9.12}}}
        features = scan_features("600630.SH", no_book, {}, at(10, 0))
        self.assertFalse(features["sealed"])
        plan = watch("600630.SH", "relay_fast_seal", {"seal_amount_max": 3e8, "fail_amount": 6e8})
        signals = teacher_review_signals(plan, no_book, {"vwap": 10.0}, None, at(10, 0))
        gap = [item for item in signals if item["signal_type"] == "data_issue"]
        self.assertIn("book", gap[0]["conditions"]["teacher_review"]["missing"])
        self.assertNotIn("entry", [item["signal_type"] for item in signals])

    def test_tencent_row_and_previous_scan_fill_missing_licensed_fields(self):
        tencent_only = {
            "price": 10.5, "pct_change": 5.0, "volume_ratio": 1.8, "turnover_rate": 3.0,
            "price_source": "tencent_batched_watch_quote", "price_freshness": {"status": "fresh"},
            "raw": {"watch_quote": {"pre_close": 10.0, "cumulative_amount": 2e8, "cumulative_volume_lot": 190000,
                                    "book_side": "two_sided",
                                    "raw_fields": ["1", "x", "0", "10.5", "10.0", "10.1"] + ["0"] * 27 + ["10.6", "9.95"]}},
        }
        features = scan_features("000001.SZ", tencent_only, None, at(10, 30), previous_quote={"price": 10.45})
        self.assertEqual((features["pre_close"], features["open"], features["high"], features["low"]), (10.0, 10.1, 10.6, 9.95))
        self.assertEqual(features["sources"]["pre_close"], "tencent")
        self.assertAlmostEqual(features["vwap"], 2e8 / 19000000, places=3)
        self.assertEqual(features["sources"]["surge"], "volume_ratio")
        self.assertTrue(features["not_falling"])
        self.assertEqual(features["sources"]["not_falling"], "previous_scan")

    def test_snapshot_tape_rebuilds_five_minute_return_and_volume_burst(self):
        tape = SnapshotTape()
        start = at(9, 30)
        # 10-second scans: 1000 lots per minute for 8 minutes, then 4000 lots in the last minute.
        for step in range(0, 55):
            seconds = step * 10
            volume = 1000 * seconds / 60 if seconds <= 480 else 8000 + 4000 * (seconds - 480) / 60
            tape.observe("000001.SZ", start + timedelta(seconds=seconds), 10 + seconds / 600, volume, "longhu")
        view = tape.features("000001.SZ", start + timedelta(seconds=540))
        self.assertAlmostEqual(view["return_5m_pct"], (10.9 / 10.4 - 1) * 100, places=3)
        self.assertAlmostEqual(view["minute_volume_multiple"], 4.0, places=1)

    def test_snapshot_tape_never_differences_volumes_across_sources_or_days(self):
        tape = SnapshotTape()
        start = at(9, 30)
        for step in range(0, 40):
            source = "longhu" if step % 2 else "tencent"
            tape.observe("000001.SZ", start + timedelta(seconds=step * 10), 10.0, 100.0 * step * (100 if source == "tencent" else 1), source)
        # Only the latest sample's source (Longhu, lots) is differenced: a steady 1x, not a 100x unit jump.
        self.assertAlmostEqual(tape.features("000001.SZ", start + timedelta(seconds=390))["minute_volume_multiple"], 1.0)
        self.assertEqual(tape.features("000001.SZ", at(9, 31, date(2026, 9, 23))), {})
        tape.observe("000001.SZ", at(9, 31, date(2026, 9, 23)), 11.0, 10.0, "longhu")
        self.assertEqual(tape.features("000001.SZ", at(9, 31, date(2026, 9, 23)))["samples"], 1)

    def test_teacher_hook_takes_minute_values_from_the_tape_without_minute_features(self):
        tape = SnapshotTape()
        plan = watch("001368.SZ", "prior_high_breakout", {"prior_high": 10.3, "floor_ma": 10}, {"floor_level": 9.5})
        signals: list[dict] = []
        for step in range(1, 37):
            price = round(10.0 + step * 0.01, 2)
            # 1000 lots a minute for five minutes, then 5000 a minute.
            lots = 1000 * step / 6 if step <= 30 else 5000 + 5000 * (step - 30) / 6
            row = quote(price, 9.8, amount=lots * 100 * 10.0, volume_lot=lots, volume_ratio=1.8)
            signals = teacher_review_signals(plan, row, None, None, at(10, 0) + timedelta(seconds=step * 10), tape=tape)
        self.assertEqual(signals[0]["signal_key"], "001368.SZ:entry:teacher_review:prior_high_breakout")
        features = signals[0]["conditions"]["teacher_review"]["features"]
        self.assertEqual(features["sources"]["surge"], "snapshot_tape")
        self.assertEqual(features["sources"]["not_falling"], "snapshot_tape")
        self.assertAlmostEqual(features["tape"]["minute_volume_multiple"], 5.0, places=1)

    def test_unfresh_licensed_row_supplies_session_constants(self):
        all_a_only = {"price": 25.0, "pct_change": 3.9, "volume_ratio": None, "raw": {
            "longhu_watch_quote_unfresh": {"pre_close": 24.06, "open": 24.5, "volume_ratio": 1.7, "amount": 3e8,
                                           "volume": 120000, "order_book": {"book_side": "two_sided"}}}}
        features = scan_features("001216.SZ", all_a_only, None, at(9, 40))
        self.assertEqual((features["pre_close"], features["open"], features["volume_ratio"]), (24.06, 24.5, 1.7))
        self.assertEqual(features["sources"]["open"], "longhu_unfresh")
        self.assertEqual(features["book"], "longhu_unfresh")

    def test_missing_vwap_never_invalidates_a_plan(self):
        bare = {"price": 9.0, "pct_change": -1.0, "raw": {}}
        plan = watch("002285.SZ", "relay_acceleration",
                     {"seal_amount_max": 4e8, "entry_amount_range": [2e8, 3e8], "entry_pct_min": 7.0, "fail_amount": 8.59e8})
        signals = teacher_review_signals(plan, bare, None, None, at(10, 0))
        self.assertEqual([item["signal_type"] for item in signals], ["data_issue"])

    def test_triggers_confirm_on_the_first_scan(self):
        plan = watch("001216.SZ", "relay_one_word", {"auction_amount_min": 5e8, "turnover_max_pct": 12.0, "prior_high": 24.26})
        signal = teacher_review_signals(plan, quote(26.47, 24.06, amount=6e8, volume_lot=226700, turnover=3.0, sealed=True),
                                        {"vwap": 26.47}, None, at(9, 30))[0]
        from app.intraday_signal_policy import signal_event_state
        self.assertEqual(signal_event_state(signal, observed_at=at(9, 30), latest_event_at=None, last_key_alerted_at=None,
                                            last_symbol_watch_alerted_at=None), "confirmed")

    def test_another_rules_watch_alert_does_not_mute_a_teacher_invalidation(self):
        from app.intraday_signal_policy import signal_event_state
        params = {"seal_amount_max": 4e8, "entry_amount_range": [2e8, 3e8], "entry_pct_min": 7.0, "fail_amount": 8.59e8}
        invalid = teacher_review_signals(watch("002285.SZ", "relay_acceleration", params),
                                         quote(3.0, 2.89, amount=9e8, volume_lot=3000000), {"vwap": 3.02}, None, at(11, 0))[0]
        self.assertEqual(signal_event_state(invalid, observed_at=at(11, 0), latest_event_at=None, last_key_alerted_at=None,
                                            last_symbol_watch_alerted_at=at(10, 58)), "confirmed")

    def test_evaluate_reports_gating_and_reference_conditions(self):
        plan = watch("600630.SH", "relay_expect_touch", {"entry_pct_min": 5.0})["metadata"]["teacher_review"]
        features = scan_features("600630.SH", quote(9.8, 9.12, amount=3e8, volume_lot=3e5, volume_ratio=2.1),
                                 {"vwap": 9.6, "minute_volume_multiple": 2.4}, at(10, 0))
        result = evaluate(plan, features)
        self.assertEqual(result["action"], "entry")
        self.assertFalse([item for item in result["signals"] if not item["gating"]][0]["pass"])


class PipelineIntegrationTests(unittest.TestCase):
    def _signal(self) -> dict:
        plan = watch("001216.SZ", "relay_one_word", {"auction_amount_min": 5e8, "turnover_max_pct": 12.0, "prior_high": 24.26})
        return teacher_review_signals(plan, quote(26.47, 24.06, amount=6e8, volume_lot=226700, turnover=3.0, sealed=True),
                                      {"vwap": 26.47}, None, at(9, 30))[0]

    def test_generation_appends_teacher_candidates(self):
        plan = watch("001216.SZ", "relay_one_word", {"auction_amount_min": 5e8, "turnover_max_pct": 12.0, "prior_high": 24.26})
        dependencies = IntradaySignalGenerationDependencies(
            base_rules=lambda *args: [], shadow_signal=lambda *args: None, rebound_signal=lambda *args: None,
            rebound_failure_signal=lambda *args: None, eac_acceptance=lambda *args, **kwargs: {},
            teacher_review_signal=teacher_review_signals,
        )
        signals = generate_intraday_signals(
            watch=plan, symbol="001216.SZ", quote=quote(26.47, 24.06, amount=6e8, volume_lot=226700, turnover=3.0, sealed=True),
            previous_quote=None, daily_factors={}, minute_features={"vwap": 26.47}, peer_context=None,
            shadow_prior=None, rebound_prior=None, first_eac=None, observed_at=at(9, 30), dependencies=dependencies,
        )
        self.assertEqual([item["policy_profile"] for item in signals], ["teacher_review"])
        self.assertTrue(signals[0]["independent_confirmation"])

    def test_policy_accepts_fresh_longhu_and_keeps_limit_up_advisory(self):
        signal = self._signal()
        live_quote = quote(26.47, 24.06, amount=6e8, volume_lot=226700, sealed=True)
        live_quote["limit_up"] = 26.47
        gate = live_policy_gate(signal, {"symbol": "001216.SZ"}, live_quote, {"status": "ok"},
                                {"status": "available", "market_state": "neutral"}, {"status": "confirmed"})
        self.assertTrue(gate["allow_confirmation"], gate)
        self.assertIn("limit_up_may_be_unbuyable", gate["advisory_reason_codes"])
        default = live_policy_gate({**signal, "policy_profile": None}, {"symbol": "001216.SZ"}, live_quote, {"status": "ok"},
                                   {"status": "available", "market_state": "neutral"}, {"status": "confirmed"})
        self.assertFalse(default["allow_confirmation"])

    def test_policy_only_blocks_teacher_prompts_without_a_price(self):
        stale = quote(26.47, 24.06, amount=6e8, volume_lot=226700, sealed=True)
        stale["price_freshness"] = {"status": "stale_timestamp"}
        gate = live_policy_gate(self._signal(), {"symbol": "001216.SZ"}, stale, {"status": "ok"},
                                {"status": "available"}, {"status": "mismatch"})
        self.assertTrue(gate["allow_confirmation"])
        self.assertIn("cross_source_price_mismatch", gate["advisory_reason_codes"])
        blind = live_policy_gate(self._signal(), {"symbol": "001216.SZ"}, {"price": None}, {}, {}, {})
        self.assertFalse(blind["allow_confirmation"])

    def test_alert_text_carries_teacher_quote_and_conditions(self):
        signal = {**self._signal(), "observed_at": at(9, 30)}
        signal["conditions"]["policy_gate"] = {"advisory_reason_codes": ["limit_up_may_be_unbuyable"]}
        text = intraday_alert_text(signal, {"label": "华瓷股份"}, {"name": "华瓷股份"}, None)
        self.assertIn("【老师复盘｜条件触发】", text)
        self.assertIn("老师原话：老师原话", text)
        self.assertIn("✔ 竞价成交额≥5亿", text)
        self.assertIn("不构成交易指令", text)


class BarCompletenessTests(unittest.TestCase):
    def test_a_missing_session_blocks_the_plan_but_a_suspension_or_listing_does_not(self):
        from app.teacher_review_repository import calendar_gaps
        sessions = [date(2026, 9, day) for day in (15, 16, 17, 18, 21)]
        bars_present = {date(2026, 9, 15), date(2026, 9, 18), date(2026, 9, 21)}
        self.assertEqual(calendar_gaps(bars_present, {date(2026, 9, 16)}, sessions), [date(2026, 9, 17)])
        self.assertEqual(calendar_gaps(bars_present | {date(2026, 9, 17)}, {date(2026, 9, 16)}, sessions), [])
        self.assertEqual(calendar_gaps({date(2026, 9, 18), date(2026, 9, 21)}, set(), sessions), [])


class CompositionTests(unittest.TestCase):
    def test_longhu_minute_batch_fans_out_and_isolates_failures(self):
        from app.longhu_vendor_source import _minutes_batch

        def fetch(symbol):
            if symbol.startswith("600"):
                raise RuntimeError("stale")
            return [{"symbol": symbol}]

        symbols = [f"{index:06d}.SZ" for index in range(1, 320)] + ["600000.SH", "bad", "000001.SZ"]
        result = _minutes_batch(fetch, symbols, 32)
        self.assertEqual(len(result), 300)
        self.assertEqual(result["000001.SZ"], [{"symbol": "000001.SZ"}])
        self.assertNotIn("600000.SH", result)
        small = _minutes_batch(fetch, ["600000.SH", "bad", "300476"], 8)
        self.assertTrue(small["600000.SH"].startswith("RuntimeError"))
        self.assertEqual(small["bad"], "unsupported Longhu stock symbol")
        self.assertEqual(small["300476"], [{"symbol": "300476"}])

    def test_longhu_minute_batch_returns_finished_symbols_at_its_deadline(self):
        import time as _time
        from app.longhu_vendor_source import _minutes_batch

        def fetch(symbol):
            if symbol == "000002.SZ":
                _time.sleep(1.0)
            return [{"symbol": symbol}]

        started = _time.monotonic()
        result = _minutes_batch(fetch, ["000001.SZ", "000002.SZ"], 2, deadline_seconds=0.3)
        self.assertLess(_time.monotonic() - started, 0.8)
        self.assertEqual(result["000001.SZ"], [{"symbol": "000001.SZ"}])
        self.assertEqual(result["000002.SZ"], "minute_batch_deadline_exceeded")

    def test_composition_root_builds_service_dependencies(self):
        from app import main
        dependencies = main._teacher_review_dependencies()
        self.assertIsInstance(dependencies, service.TeacherReviewDependencies)
        self.assertIsNone(dependencies.hydrate_history)
        post_close = main._post_close_refresh_dependencies()
        self.assertIsNotNone(post_close.teacher_review_roll)


class ConfluenceTests(unittest.TestCase):
    def test_book_is_scoped_to_one_session_and_rebuilt_from_evidence(self):
        from app.strategy_confluence import ConfluenceBook, teacher_confluence_line, teacher_plans_for_day
        book = ConfluenceBook()
        book.hydrate_xiaojie(date(2026, 9, 22), [{"symbol": "002285.SZ", "mode": "潜龙出海_swing"}])
        book.note_xiaojie(date(2026, 9, 22), [{"symbol": "002285.SZ", "mode": "leader_divergence"}])
        self.assertEqual(book.xiaojie_modes(date(2026, 9, 22), "002285.sz"), ["leader_divergence", "潜龙出海_swing"])
        self.assertEqual(book.xiaojie_modes(date(2026, 9, 23), "002285.SZ"), [])
        rows = [watch("002285.SZ", "relay_acceleration", {}), watch("600630.SH", "relay_race", {}, session="2026-09-23")]
        plans = teacher_plans_for_day(rows, date(2026, 9, 22))
        self.assertEqual(list(plans), ["002285.SZ"])
        book.set_teacher_plans(date(2026, 9, 22), plans, at(9, 30))
        self.assertIn("老师复盘", teacher_confluence_line(book.teacher_plan(date(2026, 9, 22), "002285.SZ")))

    def test_teacher_alert_carries_confluence_lines(self):
        plan = watch("001216.SZ", "relay_one_word", {"auction_amount_min": 5e8, "turnover_max_pct": 12.0, "prior_high": 24.26})
        signal = {**teacher_review_signals(plan, quote(26.47, 24.06, amount=6e8, volume_lot=226700, turnover=3.0, sealed=True),
                                           {"vwap": 26.47}, None, at(9, 30))[0], "observed_at": at(9, 30)}
        text = intraday_alert_text(signal, {}, {"name": "华瓷股份"}, None, confluence=["多策略共振：小杰龙头 潜龙出海_swing 今日同样选中"])
        self.assertIn("多策略共振：小杰龙头", text)


class FakeRepo:
    """In-memory stand-ins for teacher_review_repository (database argument ignored)."""

    def __init__(self, sessions: list[date]):
        self.sessions, self.packs, self.applied, self.settlements, self.retired = sessions, [], [], [], []

    def pack_record(self, _db, pack_id):
        return next(({"payload": {"import": {}}, "available_at": item["available_at"]}
                     for item in self.packs if item["pack"]["pack_id"] == pack_id), None)

    def open_sessions(self, _db, *, after, count=8):
        return [day for day in self.sessions if day > after][:count]

    def sessions_between(self, _db, *, after, through):
        return [day for day in self.sessions if after < day <= through]

    def apply_session_plans(self, _db, plans, **_kwargs):
        self.applied.append([plan["ts_code"] for plan in plans])
        return {"admitted": [plan["ts_code"] for plan in plans], "merged": [], "overflow": [], "skipped": [], "new_rows": []}

    def persist_pack(self, _db, pack, *, review_close, available_at, import_summary):
        self.packs.append({"pack": pack, "available_at": available_at})
        return True

    def recent_packs(self, _db, *, since):
        return list(self.packs)

    def retire_plans(self, _db, *, keep, retired_at, only_pack_ids=None):
        self.retired.append(sorted(keep))
        return {"disabled": [], "stripped": []}

    def session_events(self, _db, session_date):
        return []

    def plan_bars(self, _db, symbols, *, through, limit=260):
        rows = [bar for bar in bars([20.0 + 0.01 * i for i in range(75)], start=date(2026, 6, 15))
                if bar["date"] <= through.strftime("%Y%m%d")]
        return {symbol: {"status": "ok", "bars": rows, "raw_close": rows[-1]["close"], "trading_date": through}
                for symbol in symbols}

    def minute_period_bars(self, _db, symbol, *, through, period, sessions=12):
        return []

    def session_bars(self, _db, symbols, trade_date):
        return {symbol: {"open": 10.0, "high": 11.0, "low": 9.9, "close": 11.0, "pre_close": 10.0, "pct": 10.0,
                         "amount": 1e8, "limit_up_price": 11.0} for symbol in symbols}

    def first_limit_up_times(self, _db, symbols, trade_date):
        return {}

    def xiaojie_session_modes(self, _db, trade_date):
        return {"002285.SZ": ["潜龙出海_swing"]}

    def persist_settlement(self, _db, session_date, settlement, *, available_at):
        self.settlements.append(settlement)
        return True


class ServiceTests(unittest.TestCase):
    sessions = [date(2026, 9, 22), date(2026, 9, 23), date(2026, 9, 24), date(2026, 9, 25)]

    def _deps(self, now: datetime, alerts: list[str]) -> service.TeacherReviewDependencies:
        async def run_database(action, timeout_seconds=30):
            return action()

        async def send(text):
            alerts.append(text)
            return {"status": "sent"}

        return service.TeacherReviewDependencies(
            database=None, run_database=run_database, now_utc=lambda: now, send_alert=send, max_symbols=lambda: 100, exchange_for=lambda symbol: symbol[-2:],
        )

    def test_import_targets_first_session_after_availability_and_orders_relay_first(self):
        fake, alerts = FakeRepo(self.sessions), []
        pack = load_pack()
        with patch.multiple(service.repo, **{name: getattr(fake, name) for name in (
                "pack_record", "open_sessions", "apply_session_plans", "persist_pack", "plan_bars", "minute_period_bars")}):
            with patch.object(service, "plan_stock", side_effect=lambda stock, rows, divergence=None: {
                    "levels": {"date": rows[-1]["date"]}, "extra": {}, "setup": "", "checklist": []}):
                result = asyncio.run(service.import_pack(pack, self._deps(at(21, 40, date(2026, 9, 21)), alerts)))
        self.assertEqual(result["status"], "imported")
        self.assertEqual(result["session_date"], "2026-09-22")
        planned = fake.applied[0]
        kinds = [CATALOG[next(s["playbook"] for s in pack["stocks"] if s["ts_code"] == code)]["kind"] for code in planned]
        self.assertEqual(kinds, sorted(kinds, key=lambda kind: kind != "relay"))
        self.assertNotIn("600127.SH", planned)  # rejected -> record only, never watched
        self.assertTrue(alerts and alerts[0].startswith("【老师复盘入池】"))

    def test_late_import_skips_to_the_next_session_and_expires_relays(self):
        fake, alerts = FakeRepo(self.sessions), []
        with patch.multiple(service.repo, **{name: getattr(fake, name) for name in (
                "pack_record", "open_sessions", "apply_session_plans", "persist_pack", "plan_bars", "minute_period_bars")}):
            with patch.object(service, "plan_stock", side_effect=lambda stock, rows, divergence=None: {
                    "levels": {}, "extra": {}, "setup": "", "checklist": []}):
                result = asyncio.run(service.import_pack(load_pack(), self._deps(at(9, 40, date(2026, 9, 22)), alerts)))
        self.assertEqual(result["session_date"], "2026-09-23")
        self.assertEqual(result["session_index"], 2)
        self.assertNotIn("002285.SZ", fake.applied[0])  # relay valid for one session only

    def test_missing_sessions_are_repaired_through_the_data_plane_then_reread(self):
        fake, repaired = FakeRepo(self.sessions), []
        calls = {"n": 0}
        good = fake.plan_bars

        def flaky_plan_bars(_db, symbols, *, through, limit=260):
            calls["n"] += 1
            if calls["n"] == 1:
                return {symbol: {"status": "unavailable", "flags": ["bar_gaps:2026-09-18,2026-09-17"]} for symbol in symbols}
            return good(_db, symbols, through=through, limit=limit)

        async def repair(day):
            repaired.append(day)
            return {"status": "completed"}

        deps = self._deps(at(21, 40, date(2026, 9, 21)), [])
        deps = service.TeacherReviewDependencies(**{**deps.__dict__, "repair_daily": repair})
        with patch.multiple(service.repo, plan_bars=flaky_plan_bars, minute_period_bars=fake.minute_period_bars):
            with patch.object(service, "plan_stock", side_effect=lambda stock, rows, divergence=None: {
                    "levels": {}, "extra": {}, "setup": "", "checklist": []}):
                plans, failures = asyncio.run(service.build_session_plans(
                    load_pack(), date(2026, 9, 22), 1, date(2026, 9, 21), deps))
        self.assertEqual(sorted(repaired, reverse=True), [date(2026, 9, 21), date(2026, 9, 18), date(2026, 9, 17)])
        self.assertEqual(len(plans), 36)
        self.assertEqual([item["code"] for item in failures], ["*"])

    def test_a_superseded_pack_is_not_rolled_or_settled(self):
        fake, alerts = FakeRepo(self.sessions), []
        old = load_pack()
        new = {**copy.deepcopy(old), "pack_id": "abcdef0123456789", "supersedes": [old["pack_id"]]}
        records = [{"pack": old, "available_at": at(20, 0, date(2026, 9, 21))},
                   {"pack": new, "available_at": at(23, 0, date(2026, 9, 21))}]
        built = []

        async def fake_build(pack, *args):
            built.append(pack["pack_id"])
            return [], []

        with patch.multiple(service.repo, **{name: getattr(fake, name) for name in (
                "open_sessions", "sessions_between", "apply_session_plans", "retire_plans", "persist_settlement")},
                recent_packs=lambda _db, since: records):
            with patch.object(service, "build_session_plans", side_effect=fake_build):
                asyncio.run(service.roll(date(2026, 9, 21), self._deps(at(16, 5, date(2026, 9, 21)), alerts)))
        self.assertEqual(built, ["abcdef0123456789"])

    def test_invalid_pack_is_rejected_without_side_effects(self):
        pack = copy.deepcopy(load_pack())
        pack["schema"] = "other"
        result = asyncio.run(service.import_pack(pack, self._deps(at(21, 0, date(2026, 9, 21)), [])))
        self.assertEqual(result["status"], "rejected")

    def test_forecast_checks(self):
        bar = {"close": 3.18, "high": 3.18, "limit_up_price": 3.18}
        bars_by_code = {"002285": bar, "603230": {"close": 15.0, "high": 16.31, "limit_up_price": 16.31}}
        self.assertEqual(service.check_forecast({"kind": "race_loses", "loser": "603230", "winner": "002285"},
                                                bars_by_code, {}, None)[1], True)
        both = {**bars_by_code, "603230": {"close": 16.31, "high": 16.31, "limit_up_price": 16.31}}
        firsts = {"603230": at(9, 30), "002285": at(13, 17)}
        self.assertEqual(service.check_forecast({"kind": "race_loses", "loser": "603230", "winner": "002285"},
                                                both, firsts, None)[1], False)
        self.assertEqual(service.check_forecast({"kind": "touched_limit", "code": "603230"}, bars_by_code, {}, None)[1], True)
        self.assertIsNone(service.check_forecast({"kind": "sector_limit_ups_at_least", "sector": "大金融", "min": 3},
                                                 {}, {}, None)[1])
        self.assertEqual(service.check_forecast({"kind": "index_close_at_least", "level": 4000}, {}, {}, 3990.0)[1], False)

    def test_settlement_reports_confluence_with_leader_flow(self):
        fake = FakeRepo(self.sessions)
        pack = load_pack()
        with patch.multiple(service.repo, **{name: getattr(fake, name) for name in (
                "session_bars", "first_limit_up_times", "xiaojie_session_modes", "session_events")}):
            settled = asyncio.run(service.settle_pack(pack, date(2026, 9, 22), 1, 1, self._deps(at(16, 0), [])))
        shilian = next(stock for stock in settled["stocks"] if stock["code"] == "002285")
        self.assertEqual(shilian["confluence"]["xiaojie_modes"], ["潜龙出海_swing"])
        self.assertEqual(settled["confluence"]["count"], 1)
        self.assertIn("共振", service.settlement_text(date(2026, 9, 22), [settled]))


@unittest.skipUnless(os.getenv("PGHOST"), "requires the compose PostgreSQL service")
class RepositoryIntegrationTests(unittest.TestCase):
    symbols = ("999981.SZ", "999982.SZ", "999983.SZ")

    def setUp(self):
        from app.main import db
        from app import teacher_review_repository as repo
        self.db, self.repo = db, repo
        self._cleanup()
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        with self.db.transaction() as connection:
            connection.execute("DELETE FROM quant.intraday_watchlists WHERE symbol=ANY(%s)", (list(self.symbols),))
            connection.execute("DELETE FROM quant.instruments WHERE symbol=ANY(%s)", (list(self.symbols),))
            connection.execute("DELETE FROM quant.raw_market_observations WHERE provider_key='teacher_review' "
                               "AND (symbol='analyst:itougu-integration' OR payload->'pack'->>'pack_id'='deadbeefcafe')")

    def _plan(self, symbol: str) -> dict:
        return {"ts_code": symbol, "name": f"测{symbol[:6]}", "label": f"测{symbol[:6]}·复盘0921",
                "metadata": {"status": "active", "session_date": "2099-01-02", "playbook": "relay_race"}}

    def test_bridge_merges_only_its_key_admits_within_capacity_and_retires_its_own_rows(self):
        from app.instrument_registry import InstrumentRecord, ensure_instruments
        with self.db.transaction() as connection:
            ensure_instruments(connection, [InstrumentRecord(symbol=self.symbols[0], exchange="SZ", name="手工",
                                                             source="test")], source="test")
            connection.execute(
                """INSERT INTO quant.intraday_watchlists(symbol,label,enabled,metadata,entry_price)
                   VALUES(%s,'手工',true,'{"source":"manual","note":"keep"}'::jsonb,10.5)""", (self.symbols[0],))
            enabled = connection.execute("SELECT count(*)::int n FROM quant.intraday_watchlists WHERE enabled").fetchone()["n"]
        result = self.repo.apply_session_plans(
            self.db, [self._plan(symbol) for symbol in self.symbols], max_symbols=enabled + 1, reserve=0,
            exchange_for=lambda symbol: symbol[-2:],
        )
        self.assertEqual(result["merged"], [self.symbols[0]])
        self.assertEqual(result["admitted"], [self.symbols[1]])
        self.assertEqual(result["overflow"], [self.symbols[2]])
        with self.db.transaction() as connection:
            manual = connection.execute("SELECT label,entry_price,metadata FROM quant.intraday_watchlists WHERE symbol=%s",
                                        (self.symbols[0],)).fetchone()
        self.assertEqual(manual["label"], "手工")
        self.assertEqual(float(manual["entry_price"]), 10.5)
        self.assertEqual(manual["metadata"]["note"], "keep")
        self.assertEqual(manual["metadata"]["teacher_review"]["playbook"], "relay_race")
        retired = self.repo.retire_plans(self.db, keep=set(), retired_at=datetime.now(timezone.utc))
        self.assertIn(self.symbols[1], retired["disabled"])
        self.assertIn(self.symbols[0], retired["stripped"])
        with self.db.transaction() as connection:
            rows = {row["symbol"]: row for row in connection.execute(
                "SELECT symbol,enabled,metadata FROM quant.intraday_watchlists WHERE symbol=ANY(%s)",
                (list(self.symbols),)).fetchall()}
        self.assertNotIn("teacher_review", rows[self.symbols[0]]["metadata"])
        self.assertTrue(rows[self.symbols[0]]["metadata"]["note"])
        self.assertFalse(rows[self.symbols[1]]["enabled"])
        self.assertEqual(rows[self.symbols[1]]["metadata"]["teacher_review"]["status"], "expired")

    def test_pack_archive_is_idempotent_by_pack_id(self):
        pack = {"pack_id": "deadbeefcafe", "analyst": {"analyst_id": "itougu-integration"}, "review_date": "2099-01-01",
                "stocks": []}
        close = datetime(2099, 1, 1, 15, 0, tzinfo=CN)
        persist = partial(self.repo.persist_pack, self.db, pack, review_close=close,
                          available_at=close + timedelta(hours=5), import_summary={"status": "imported"})
        self.assertTrue(persist())
        self.assertFalse(persist())
        self.assertIsNotNone(self.repo.pack_record(self.db, "deadbeefcafe"))
        self.assertEqual([item["pack"]["pack_id"] for item in self.repo.recent_packs(self.db, since=date(2098, 12, 31))
                          if item["pack"]["pack_id"] == "deadbeefcafe"], ["deadbeefcafe"])


@unittest.skipUnless(os.getenv("PGHOST"), "requires the compose PostgreSQL service")
class ScanPersistenceIntegrationTests(unittest.TestCase):
    """Run teacher candidates through the real policy/state/episode/event writer, then roll back."""

    class Rollback(Exception):
        pass

    def test_teacher_entry_and_data_gap_persist_as_confirmed_events(self):
        from app import main
        from app.instrument_registry import InstrumentRecord, ensure_instruments
        from app.intraday_signal_event_persistence import persist_generated_signals
        dependencies = main._intraday_scan_persistence_dependencies().signal_dependencies
        symbol = "999984.SZ"
        plan = watch(symbol, "relay_one_word", {"auction_amount_min": 5e8, "turnover_max_pct": 12.0, "prior_high": 24.26})
        live_quote = quote(26.47, 24.06, amount=6e8, volume_lot=226700, turnover=3.0, sealed=True)
        signals = teacher_review_signals(plan, live_quote, {"vwap": 26.47}, None, at(9, 30))
        signals += teacher_review_signals(watch(symbol, "relay_race", {}),
                                          {"price": 25.0, "pct_change": 3.9, "raw": {}}, None, None, at(9, 30))
        results = []
        try:
            with main.db.transaction() as connection:
                ensure_instruments(connection, [InstrumentRecord(symbol=symbol, exchange="SZ", name="测", source="test")],
                                   source="test")
                scan_id = __import__("uuid").uuid4()
                connection.execute(
                    """INSERT INTO quant.intraday_scan_runs(scan_id,observed_at,status,requested_symbols,source_status,summary)
                       VALUES(%s,%s,'completed','[]'::jsonb,'{}'::jsonb,'{}'::jsonb)""", (scan_id, at(9, 30)))
                state = dependencies.load_event_state(connection, [s["signal_key"] for s in signals], symbol,
                                                      session_start=at(9, 0))
                results = persist_generated_signals(
                    connection, scan_id=scan_id, observed_at=at(9, 30), symbol=symbol, watch=plan,
                    quote=live_quote, daily_factors={"status": "ok"}, minute_feature={"vwap": 26.47}, peer_context=None,
                    market_context={"status": "missing"}, fast_confirmation={"status": "mismatch"},
                    order_book_feature={}, realtime_minute=None, paper_position=None, portfolio_snapshot={},
                    candidate_sector_keys=(), probability_profiles={}, generated_signals=signals,
                    existing_event_state=state, confirmation_window=timedelta(minutes=5),
                    factor_contract_version="test", dependencies=dependencies.signal_event_persistence_dependencies,
                )
                raise self.Rollback
        except self.Rollback:
            pass
        states = {item["signal_type"]: item["state"] for item in results}
        self.assertEqual(states["entry"], "confirmed")
        self.assertIn("data_issue", states)
        text = intraday_alert_text({**results[0], "observed_at": at(9, 30)}, plan, live_quote, None)
        self.assertIn("【老师复盘｜条件触发】", text)


if __name__ == "__main__":
    unittest.main()
