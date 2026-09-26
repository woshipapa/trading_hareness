"""A looser threshold is judged on both what it catches and what it lets in."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from app.strategy_parameter_sweep import (
    GRID, first_entry, summarize, sweep_lines, sweep_symbol, variants,
)
from app.teacher_review_playbooks import DEFAULTS

CN = timezone(timedelta(hours=8))


def snapshot(at, *, price, volume_ratio, prior_high=11.0, pre_close=10.0):
    plan = {"playbook": "prior_high_breakout", "status": "active", "session_date": "2026-09-22",
            "name": "测试", "params": {"prior_high": prior_high}, "extra": {}, "pack_id": "p1"}
    quote = {"symbol": "000001.SZ", "price": price, "pct_change": round((price / pre_close - 1) * 100, 2),
             "volume_ratio": volume_ratio,
             "raw": {"watch_quote": {"pre_close": pre_close, "open": pre_close, "high": price,
                                     "low": pre_close, "amount": 4e8, "vwap": price - 0.05}}}
    minute = {"vwap": price - 0.05, "volume_lot": 120000, "surge": True}
    return {"observed_at": at, "inputs": {"watch": {"symbol": "000001.SZ", "metadata": {"teacher_review": plan}},
                                          "quote": quote, "previous_quote": None, "minute_features": minute}}


def rows(prices, volume_ratio):
    """Samples two minutes apart, so the five-minute window exists from the 4th."""
    start = datetime(2026, 9, 22, 9, 40, tzinfo=CN)
    return [snapshot(start + timedelta(minutes=2 * index), price=price, volume_ratio=volume_ratio)
            for index, price in enumerate(prices)]


class VariantTests(unittest.TestCase):
    def test_the_live_table_is_always_the_first_variant(self):
        built = variants()
        self.assertEqual(built[0]["label"], "live")
        self.assertEqual(built[0]["overrides"], {})
        self.assertEqual(built[0]["defaults"]["vol_ratio_min"], DEFAULTS["vol_ratio_min"])

    def test_each_variant_changes_exactly_one_threshold(self):
        for variant in variants()[1:]:
            self.assertEqual(len(variant["overrides"]), 1)
            name, value = next(iter(variant["overrides"].items()))
            self.assertEqual(variant["defaults"][name], value)
            self.assertNotEqual(DEFAULTS[name], value)

    def test_the_live_value_is_never_repeated_as_its_own_variant(self):
        labels = [variant["label"] for variant in variants()]
        self.assertNotIn(f"vol_ratio_min={DEFAULTS['vol_ratio_min']}", labels)

    def test_the_grid_never_sweeps_a_price(self):
        self.assertNotIn("prior_high", GRID)
        self.assertNotIn("platform_high", GRID)

    def test_the_live_defaults_are_never_mutated_by_a_variant(self):
        before = dict(DEFAULTS)
        built = variants()
        built[1]["defaults"]["vol_ratio_min"] = 99
        self.assertEqual(dict(DEFAULTS), before)


class FirstEntryTests(unittest.TestCase):
    def test_a_volume_ratio_below_the_table_blocks_the_entry(self):
        blocked = rows([10.4, 10.5, 10.6, 11.5, 11.6], volume_ratio=1.1)
        self.assertIsNone(first_entry("000001.SZ", "测试", blocked, DEFAULTS))

    def test_the_same_day_enters_once_the_threshold_allows_it(self):
        blocked = rows([10.4, 10.5, 10.6, 11.5, 11.6], volume_ratio=1.1)
        entry = first_entry("000001.SZ", "测试", blocked, {**DEFAULTS, "vol_ratio_min": 1.0})
        self.assertIsNotNone(entry)
        self.assertEqual(entry["price"], 11.5)

    def test_the_first_qualifying_scan_wins_not_the_best_one(self):
        entry = first_entry("000001.SZ", "测试", rows([10.4, 10.5, 10.6, 11.2, 12.0], volume_ratio=2.0), DEFAULTS)
        self.assertEqual(entry["price"], 11.2)


class SweepTests(unittest.TestCase):
    def results(self):
        bar = {"close": 11.8, "pre_close": 10.0, "limit_up_price": 13.0}
        per_symbol = {}
        for symbol, prices, ratio in (("000001", [10.4, 10.5, 10.6, 11.5, 11.6], 1.1),
                                      ("000002", [10.3, 10.4, 10.5, 11.4, 11.7], 1.1),
                                      ("000003", [10.4, 10.5, 10.6, 11.6, 11.9], 2.0)):
            per_symbol[symbol] = sweep_symbol(f"{symbol}.SZ", "测试", rows(prices, ratio),
                                              bar=bar, benchmark_pct=1.0,
                                              grid={"vol_ratio_min": (1.0,)})
        return per_symbol

    def test_a_looser_threshold_shows_what_it_would_have_added(self):
        rows_out = summarize(self.results())
        live = next(row for row in rows_out if row["label"] == "live")
        loose = next(row for row in rows_out if row["label"] == "vol_ratio_min=1.0")
        self.assertEqual(live["entered"], 1)
        self.assertEqual(loose["entered"], 3)
        self.assertEqual(loose["vs_live_entries"], 2)

    def test_the_variant_is_measured_net_and_against_the_day(self):
        loose = next(row for row in summarize(self.results()) if row["label"] == "vol_ratio_min=1.0")
        self.assertIsNotNone(loose["net_mean_pct"])
        # Entry 11.5 -> close 11.8 is +2.6% gross; net is below it and excess
        # is below that again, because the day itself was up 1%.
        self.assertLess(loose["net_mean_pct"], 2.61)
        self.assertLess(loose["excess_mean_pct"], loose["net_mean_pct"])

    def test_a_variant_seen_by_too_few_names_is_not_reported(self):
        thin = summarize({"000001": self.results()["000001"]}, min_symbols=3)
        self.assertEqual(thin, [])

    def test_an_entry_at_the_limit_is_counted_as_unbuyable_not_as_a_win(self):
        sealed_bar = {"close": 11.5, "pre_close": 10.0, "limit_up_price": 11.5}
        per_symbol = {symbol: sweep_symbol(f"{symbol}.SZ", "测试", rows([10.4, 10.5, 10.6, 11.5, 11.5], 1.1),
                                           bar=sealed_bar, benchmark_pct=1.0, grid={"vol_ratio_min": (1.0,)})
                      for symbol in ("000001", "000002", "000003")}
        loose = next(row for row in summarize(per_symbol) if row["label"] == "vol_ratio_min=1.0")
        self.assertEqual(loose["entered"], 3)
        self.assertEqual(loose["unbuyable"], 3)
        self.assertEqual(loose["evaluable_entries"], 0)
        self.assertIsNone(loose["net_mean_pct"])

    def test_the_report_line_states_both_sides_of_the_trade_off(self):
        line = sweep_lines(summarize(self.results()))[0]
        self.assertIn("触发", line)
        self.assertIn("买不到", line)
        self.assertIn("净收益均值", line)


if __name__ == "__main__":
    unittest.main()
