"""What worked, what was missed, and which condition blocked it."""

from __future__ import annotations

import unittest
from datetime import date, datetime, timedelta, timezone

from app.strategy_outcome_measures import UNBUYABLE
from app.teacher_outcome_review import (
    BIG_MOVE_PCT, OUTCOME_LABELS, classify, gate_replay, learning, outcome_markdown, outcome_text, review_stocks,
)

CN = timezone(timedelta(hours=8))


def bar(close, *, pre_close=10.0, high=None, low=None, limit_up=None):
    return {"open": pre_close, "high": high if high is not None else close, "low": low if low is not None else close,
            "close": close, "pre_close": pre_close, "pct": round((close / pre_close - 1) * 100, 2),
            "amount": 5e8, "limit_up_price": limit_up}


def stock(code="000001", *, name="测试", playbook="platform_breakout", kind="trend", entry=None,
          entry_to_close_pct=None, invalidated=False, sealed=False, touched=False, close=10.0, high=None):
    return {"code": code, "name": name, "playbook": playbook, "kind": kind, "stance": "watch",
            "bar": bar(close, high=high), "closed_at_limit": sealed, "touched_limit": touched,
            "entry": entry, "entry_to_close_pct": entry_to_close_pct,
            "invalidated_at": "2026-09-22T14:30:00+08:00" if invalidated else None,
            "confluence": {"xiaojie_modes": []}, "first_limit_up_at": None}


class ClassifyTests(unittest.TestCase):
    def test_a_triggered_plan_that_held_into_the_close_is_a_hit(self):
        held = stock(entry={"at": "2026-09-22T10:02:00+08:00", "price": 10.2}, close=10.5)
        verdict = classify(held)
        self.assertEqual(verdict["outcome"], "hit")
        # Net of one round trip, so the reported number is under the gross 2.94%.
        self.assertLess(verdict["measures"]["net_session_return_pct"], 2.94)
        self.assertGreater(verdict["measures"]["net_session_return_pct"], 2.5)

    def test_a_triggered_plan_that_gave_it_back_is_reported_separately(self):
        faded = stock(entry={"at": "2026-09-22T10:02:00+08:00", "price": 10.8}, close=10.5)
        self.assertEqual(classify(faded)["outcome"], "triggered_faded")

    def test_an_entry_that_only_covers_the_round_trip_is_not_a_hit(self):
        # +0.1% gross is a loss once the round trip is paid.
        marginal = stock(entry={"at": "2026-09-22T10:02:00+08:00", "price": 10.49}, close=10.5)
        self.assertEqual(classify(marginal)["outcome"], "triggered_faded")

    def test_a_flag_raised_on_a_sealed_board_is_not_scored_at_all(self):
        sealed = stock(entry={"at": "2026-09-22T09:33:00+08:00", "price": 11.0, "sealed": True},
                       close=11.0, sealed=True)
        verdict = classify(sealed)
        self.assertEqual(verdict["outcome"], UNBUYABLE)
        self.assertIs(verdict["measures"]["evaluable"], False)

    def test_the_limit_price_alone_is_enough_to_tell_it_was_unbuyable(self):
        # Older archives kept no sealed flag; the price against the limit does.
        at_limit = stock(entry={"at": "2026-09-22T09:33:00+08:00", "price": 11.0}, close=11.0)
        at_limit["bar"]["limit_up_price"] = 11.0
        self.assertEqual(classify(at_limit)["outcome"], UNBUYABLE)

    def test_the_session_move_is_credited_against_the_day(self):
        held = stock(entry={"at": "2026-09-22T10:02:00+08:00", "price": 10.2}, close=10.5)
        verdict = classify(held, benchmark_pct=1.5)
        self.assertAlmostEqual(verdict["measures"]["excess_session_pct"], 2.9412 - 1.5, places=3)
        self.assertEqual(verdict["measures"]["benchmark_session_pct"], 1.5)

    def test_an_untriggered_plan_that_ran_anyway_is_the_one_to_learn_from(self):
        self.assertEqual(classify(stock(close=10.0 * (1 + BIG_MOVE_PCT / 100)))["outcome"], "missed")
        self.assertEqual(classify(stock(close=10.1))["outcome"], "filtered")

    def test_a_close_that_faded_still_counts_as_missed_when_the_day_ran(self):
        spike = classify(stock(close=10.2, high=10.9))
        self.assertEqual(spike["outcome"], "missed")
        self.assertEqual(spike["high_pct"], 9.0)
        self.assertEqual(spike["opportunity_pct"], 9.0)

    def test_a_big_move_outranks_an_intraday_invalidation(self):
        # Being stopped out does not make a 7% day a correct filter; it is still a miss.
        self.assertEqual(classify(stock(close=10.7, invalidated=True))["outcome"], "missed")
        self.assertEqual(classify(stock(close=9.8, invalidated=True))["outcome"], "invalidated")

    def test_the_teachers_own_rejections_are_scored_as_avoidance(self):
        self.assertEqual(classify(stock(kind="record", close=9.4))["outcome"], "avoided")
        self.assertEqual(classify(stock(kind="record", close=10.6))["outcome"], "avoid_missed")
        self.assertEqual(classify(stock(kind="record", close=10.0, sealed=True))["outcome"], "avoid_missed")

    def test_a_session_without_a_bar_is_never_scored(self):
        self.assertEqual(classify({"code": "000001", "kind": "trend", "bar": None})["outcome"], "no_data")


def snapshot(at, *, price, session="2026-09-22", pre_close=10.0, prior_high=11.0, vwap=None, volume_ratio=2.0):
    plan = {"playbook": "prior_high_breakout", "status": "active", "session_date": session, "name": "测试",
            "params": {"prior_high": prior_high}, "extra": {}, "pack_id": "p1"}
    quote = {"symbol": "000001.SZ", "price": price, "pct_change": round((price / pre_close - 1) * 100, 2),
             "volume_ratio": volume_ratio,
             "raw": {"watch_quote": {"pre_close": pre_close, "open": pre_close, "high": price, "low": pre_close,
                                     "amount": 4e8, "vwap": vwap if vwap is not None else price - 0.05}}}
    minute = {"vwap": (vwap if vwap is not None else price - 0.05), "volume_lot": 120000, "surge": True}
    return {"observed_at": at, "inputs": {"watch": {"symbol": "000001.SZ", "metadata": {"teacher_review": plan}},
                                          "quote": quote, "previous_quote": None, "minute_features": minute}}


class GateReplayTests(unittest.TestCase):
    def rows(self, prices, **kwargs):
        start = datetime(2026, 9, 22, 9, 35, tzinfo=CN)
        return [snapshot(start + timedelta(minutes=2 * index), price=price, **kwargs)
                for index, price in enumerate(prices)]

    def test_it_counts_which_gate_blocked_and_how_often(self):
        report = gate_replay("000001.SZ", "测试", self.rows([10.2, 10.5, 10.8]))
        self.assertEqual(report["evaluated"], 3)
        self.assertEqual(report["entry_scans"], 0)
        blocked = {gate["name"]: gate for gate in report["gates"]}
        price_gate = next(name for name in blocked if name.startswith("价格>"))
        self.assertEqual(blocked[price_gate]["blocked"], 3)
        self.assertEqual(blocked[price_gate]["share"], 100.0)

    def test_the_closest_scan_names_what_was_still_missing(self):
        report = gate_replay("000001.SZ", "测试", self.rows([10.2, 11.5], volume_ratio=0.4))
        closest = report["closest"]
        self.assertEqual(closest["at"], "09:37")
        self.assertTrue(any(item["name"].startswith("量比") for item in closest["blocked"]))
        self.assertFalse(any(item["name"].startswith("价格>") for item in closest["blocked"]))

    def test_a_plan_from_another_session_is_never_replayed(self):
        report = gate_replay("000001.SZ", "测试", self.rows([10.2], session="2026-09-19"))
        self.assertEqual(report["evaluated"], 0)
        self.assertIsNone(report["closest"])

    def test_a_missing_input_never_counts_as_an_entry(self):
        # No minute features and no volume: the live scan cannot price a vwap,
        # so it downgrades to watch however good the price looks.
        rows = self.rows([10.2, 10.4, 10.6, 11.5])
        for row in rows:
            row["inputs"]["minute_features"] = None
            row["inputs"]["quote"]["raw"]["watch_quote"].pop("amount")
        report = gate_replay("000001.SZ", "测试", rows)
        self.assertEqual(report["entry_scans"], 0)
        self.assertIn("vwap", [item["name"] for item in report["missing_inputs"]])

    def test_an_entry_is_recognised_when_every_gate_passes(self):
        # The 5-minute trend gate needs a baseline, exactly as the live tape does.
        report = gate_replay("000001.SZ", "测试", self.rows([10.2, 10.4, 10.6, 11.5]))
        self.assertEqual(report["entry_scans"], 1)
        self.assertEqual(report["closest"]["action"], "entry")


class ReportTests(unittest.TestCase):
    def packs(self):
        return [{"pack_id": "p1", "review_date": "2026-09-21", "session_index": 1, "stocks": [
            stock("000993", name="闽东电力", entry={"at": "2026-09-22T10:02:00+08:00", "price": 10.2},
                  close=10.6),
            stock("300476", name="胜宏科技", playbook="ma60_reclaim", close=10.8),
            stock("600519", name="否定票", kind="record", playbook="rejected", close=10.9),
            stock("000001", name="过滤票", close=10.05),
        ]}]

    def report(self):
        replays = {"300476": {"evaluated": 100, "gates": [
            {"name": "价格>当日MA60", "blocked": 90, "scans": 100, "share": 90.0, "last_value": "10.8 vs 11.2"}],
            "closest": {"at": "14:31", "shortfall": 1, "blocked": [{"name": "价格>当日MA60", "value": "10.8 vs 11.2"}],
                        "unknown": []}}}
        stocks = review_stocks(self.packs(), replays)
        return {"trade_date": "2026-09-22", "model_version": "t", "rules_version": "r", "packs": 1,
                "stocks": stocks, "counts": {}}

    def test_every_stock_lands_in_exactly_one_bucket(self):
        outcomes = [item["outcome"] for item in self.report()["stocks"]]
        self.assertEqual(sorted(outcomes), ["avoid_missed", "filtered", "hit", "missed"])
        self.assertTrue(set(outcomes) <= set(OUTCOME_LABELS))

    def test_the_missed_stock_carries_the_blocking_condition(self):
        missed = next(item for item in self.report()["stocks"] if item["outcome"] == "missed")
        self.assertIn("价格>当日MA60", missed["blocked_by"])
        self.assertIn("90.0%", missed["blocked_by"])

    def test_a_replayed_entry_without_a_live_push_is_flagged_as_a_defect(self):
        replays = {"300476": {"evaluated": 100, "entry_scans": 7, "gates": [],
                              "closest": {"at": "10:05", "shortfall": 0, "blocked": [], "unknown": []}}}
        missed = next(item for item in review_stocks(self.packs(), replays) if item["code"] == "300476")
        self.assertIn("没有推送", missed["blocked_by"])
        self.assertIn("不是条件太严", missed["blocked_by"])

    def test_a_missing_input_is_reported_as_a_data_gap_not_a_tight_threshold(self):
        replays = {"300476": {"evaluated": 100, "entry_scans": 0, "gates": [
            {"name": "均价上方（不能往下跌）", "blocked": 80, "scans": 100, "share": 80.0, "last_value": "x vs None"}],
            "missing_inputs": [{"name": "vwap", "scans": 80}], "closest": None}}
        missed = next(item for item in review_stocks(self.packs(), replays) if item["code"] == "300476")
        self.assertIn("输入缺失：分时均价", missed["blocked_by"])
        self.assertEqual(missed["data_gap"]["share"], 80.0)

    def test_the_feishu_summary_leads_with_results_and_stays_research_only(self):
        text = outcome_text(date(2026, 9, 22), self.report())
        self.assertIn("闽东电力", text)
        self.assertIn("★ 漏 胜宏科技", text)
        self.assertIn("研究记录，不构成交易指令。", text)

    def test_the_markdown_report_has_a_row_per_stock(self):
        page = outcome_markdown(date(2026, 9, 22), self.report())
        self.assertIn("| 300476 | 胜宏科技 |", page)
        self.assertIn(OUTCOME_LABELS["avoid_missed"], page)


class SnapshotQueryTests(unittest.TestCase):
    """PostgreSQL requires DISTINCT ON to match the leading ORDER BY exactly."""

    def query(self):
        import contextlib

        from app import teacher_review_repository as repo

        captured = {}

        class Connection:
            def execute(self, sql, params=None):
                captured["sql"], captured["params"] = sql, params
                return type("R", (), {"fetchall": staticmethod(lambda: [])})()

        class Database:
            @contextlib.contextmanager
            def transaction(self):
                yield Connection()

        repo.rule_input_snapshots(Database(), ["000001"], date(2026, 9, 22))
        return captured

    def test_the_bucket_expression_is_inlined_and_identical_in_both_clauses(self):
        captured = self.query()
        sql = captured["sql"]
        bucket = "to_timestamp(floor(extract(epoch FROM observed_at)/120)*120)"
        self.assertEqual(sql.count(bucket), 2)
        self.assertIn(f"DISTINCT ON (symbol, {bucket})", sql)
        self.assertIn(f"ORDER BY symbol, {bucket}, observed_at", sql)
        self.assertEqual(len(captured["params"]), 3)

    def test_codes_are_normalised_to_exchange_symbols(self):
        self.assertEqual(self.query()["params"][0], ["000001.SZ"])


class DeliveredEntryTests(unittest.TestCase):
    """A push the settlement missed must not be scored as a miss."""

    def packs(self):
        return [{"pack_id": "p1", "stocks": [
            stock("605058", name="澳弘电子", close=11.0),
            stock("600519", name="否定票", kind="record", playbook="rejected", close=11.0),
        ]}]

    def test_an_event_log_entry_is_adopted_when_the_settlement_has_none(self):
        from app.teacher_outcome_review import with_delivered_entries

        delivered = {"605058": {"at": "2026-09-22T10:05:00+08:00", "price": 10.5, "path": None}}
        merged = with_delivered_entries(self.packs(), delivered)[0]["stocks"]
        self.assertEqual(merged[0]["entry"]["source"], "signal_events")
        self.assertEqual(merged[0]["entry_to_close_pct"], 4.76)
        self.assertEqual(classify(merged[0])["outcome"], "hit")

    def test_a_rejected_stock_is_never_given_an_entry(self):
        from app.teacher_outcome_review import with_delivered_entries

        delivered = {"600519": {"at": "2026-09-22T10:05:00+08:00", "price": 10.5}}
        merged = with_delivered_entries(self.packs(), delivered)[0]["stocks"]
        self.assertIsNone(merged[1]["entry"])


class LearningTests(unittest.TestCase):
    def missed(self, code, gate, pct, day):
        return {"code": code, "name": code, "playbook": "platform_breakout", "outcome": "missed",
                "opportunity_pct": pct, "replay": {"gates": [{"name": gate, "share": 80.0}]}}

    def test_a_condition_that_keeps_blocking_big_moves_becomes_a_suggestion(self):
        reports = [{"trade_date": f"2026-09-{day:02d}",
                    "stocks": [self.missed(f"00000{day}", "量比≥1.5（带量）", 7.5, day)]} for day in (18, 21, 22)]
        result = learning(reports)
        self.assertEqual(result["session_count"], 3)
        suggestion = result["suggestions"][0]
        self.assertEqual(suggestion["missed_cases"], 3)
        self.assertEqual(suggestion["mean_pct"], 7.5)
        self.assertIn("量比", suggestion["note"])

    def test_one_off_blocks_are_not_turned_into_advice(self):
        result = learning([{"trade_date": "2026-09-22", "stocks": [self.missed("000001", "量比≥1.5（带量）", 6.0, 22)]}])
        self.assertEqual(result["suggestions"], [])

    def test_hit_and_miss_rates_ignore_the_teachers_rejections(self):
        stocks = [
            {"playbook": "relay_race", "outcome": "hit", "entry_to_close_pct": 4.0, "opportunity_pct": 6.0},
            {"playbook": "relay_race", "outcome": "missed", "opportunity_pct": 8.0, "replay": {}},
            {"playbook": "relay_race", "outcome": "avoided", "opportunity_pct": -1.0},
        ]
        row = learning([{"trade_date": "2026-09-22", "stocks": stocks}])["playbooks"][0]
        self.assertEqual(row["total"], 3)
        self.assertEqual(row["hit_rate_pct"], 50.0)
        self.assertEqual(row["missed_rate_pct"], 50.0)
        self.assertEqual(row["missed_mean_pct"], 8.0)


if __name__ == "__main__":
    unittest.main()


class ForwardCompletionTests(unittest.TestCase):
    """A report written at its own close cannot know the next open."""

    def previous(self, *, already_filled=False):
        measures = {"session_return_pct": 2.9, "next_open_to_close_pct": 1.0} if already_filled else {
            "session_return_pct": 2.9, "next_open_to_close_pct": None}
        return [{"trade_date": "2026-09-22", "benchmark_session_pct": 0.8, "stocks": [
            {"code": "000993", "name": "闽东电力", "outcome": "hit", "measures": measures,
             "entry": {"at": "10:02", "price": 10.2},
             "bar": {"close": 10.5, "pre_close": 10.0, "limit_up_price": 11.0}},
            {"code": "000001", "name": "过滤票", "outcome": "filtered", "entry": None, "bar": {"close": 10.05}},
        ]}]

    def run_completion(self, previous, next_bars):
        import asyncio
        from app import teacher_outcome_review as module

        saved = {}

        from types import SimpleNamespace

        async def run_database(action, timeout_seconds=30):
            return action()

        deps = SimpleNamespace(database=None, run_database=run_database,
                               now_utc=lambda: datetime(2026, 9, 23, 8, 0, tzinfo=timezone.utc))

        original_bars, original_persist = module.repo.session_bars, module.repo.persist_outcome_review
        module.repo.session_bars = lambda _db, symbols, trade_date: next_bars
        module.repo.persist_outcome_review = lambda _db, session, payload, available_at: saved.update(
            {"session": session, "payload": payload}) or True
        try:
            filled = asyncio.run(module.complete_previous(previous, date(2026, 9, 23), deps))
        finally:
            module.repo.session_bars, module.repo.persist_outcome_review = original_bars, original_persist
        return filled, saved

    def test_the_next_sessions_bars_fill_the_forward_returns(self):
        filled, saved = self.run_completion(
            self.previous(), {"000993.SZ": {"open": 10.6, "close": 11.0, "limit_up_price": 11.55}})
        self.assertEqual(filled, 1)
        self.assertEqual(saved["session"], date(2026, 9, 22))
        stock = saved["payload"]["stocks"][0]
        self.assertAlmostEqual(stock["measures"]["next_open_to_close_pct"], (11.0 / 10.6 - 1) * 100, places=3)
        # The verdict itself is decided on the session and never revised.
        self.assertEqual(stock["outcome"], "hit")
        self.assertEqual(saved["payload"]["forward_completed_on"], "2026-09-23")

    def test_a_report_already_completed_is_left_alone(self):
        filled, saved = self.run_completion(
            self.previous(already_filled=True), {"000993.SZ": {"open": 10.6, "close": 11.0}})
        self.assertEqual(filled, 0)
        self.assertEqual(saved, {})

    def test_a_symbol_without_a_next_bar_keeps_its_record_unchanged(self):
        filled, saved = self.run_completion(self.previous(), {})
        self.assertEqual(filled, 0)
        self.assertEqual(saved, {})
