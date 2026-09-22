"""Post-close review: path, sector relation, history and labels from plain inputs."""

from __future__ import annotations

import unittest
from datetime import date

from app.watch_daily_review import (history_context, intraday_path, pattern_labels, review_stock, sector_relation,
                                    summarize, tape_timeline)


def minutes(prices: list[float], vwap_lag: float = 0.0) -> list[dict]:
    rows, stamp = [], 570
    for index, price in enumerate(prices):
        t = stamp + index if index <= 120 else 780 + (index - 120)
        rows.append({"time": f"{t // 60:02d}{t % 60:02d}", "close": price, "vwap": price - vwap_lag, "volume_lot": 100 + index})
    return rows


class WatchDailyReviewTests(unittest.TestCase):
    def test_a_spike_and_fade_is_labelled(self):
        prices = [10.0 + 0.05 * i for i in range(20)] + [11.0 - 0.02 * i for i in range(221)]
        path = intraday_path(minutes(prices, vwap_lag=-0.2), 10.0)
        self.assertEqual(path["high_time"], "09:50")
        self.assertTrue(path["high_first"])
        self.assertLess(path["close_range_position"], 0.2)
        labels = pattern_labels({"gap_pct": 0.0, "close_pct": 6.6}, path, {}, {}, {})
        self.assertIn("冲高回落", labels)
        self.assertIn("平开", labels)

    def test_sector_relation_needs_overlap_and_detects_co_movement(self):
        rows = minutes([10.0 + 0.01 * i for i in range(121)])
        series = [(row["time"], (row["close"] / 10.0 - 1) * 100 * 0.5) for row in rows]
        relation = sector_relation(rows, 10.0, series, "元件")
        self.assertEqual(relation["status"], "ok")
        self.assertGreater(relation["relative_strength_pct"], 0)
        self.assertEqual(sector_relation(rows, 10.0, series[:20], "元件")["status"], "sparse_overlap")
        self.assertIsNone(relation["lead"] if relation["points"] < 24 else None)

    def test_history_and_review_record(self):
        bars = [{"date": f"2026{m:02d}{d:02d}", "open": 10, "high": 10.5 + i * 0.01, "low": 9.5, "close": 10 + i * 0.05,
                 "amount": 1e8 * (2 if i == 59 else 1), "limit_up": i == 59} for i, (m, d) in enumerate((7 + k // 28, 1 + k % 28) for k in range(60))]
        history = history_context(bars)
        self.assertEqual(history["amount_vs_20d"], 2.0)
        self.assertTrue(history["ma_bullish_order"])
        review = review_stock(symbol="000001.SZ", name="测试", trade_date=date(2026, 9, 22),
                              quote_day={"open": 11.0, "high": 11.0, "low": 11.0, "close": 11.0, "pre_close": 10.0, "limit_up_price": 11.0},
                              minutes=minutes([11.0] * 241), bars=bars, limit={"touched": True, "sealed_at_close": True, "board_opens": 0},
                              sector={"label": "元件", "sector_key": "881270"}, sector_series=[], signals=[], themes=["PCB"])
        self.assertIn("一字板", review["patterns"])
        self.assertIn("封板", review["patterns"])
        self.assertIn("放量", review["patterns"])
        self.assertEqual(summarize([review])["patterns"]["一字板"]["stocks"], ["测试"])


def tape(points: list[tuple[str, dict]]) -> list[tuple[str, dict]]:
    return [(stamp, {"p": 10.0, "vw": 9.9, **row}) for stamp, row in points]


class TapeTimelineTests(unittest.TestCase):
    def test_auction_drift_seal_episodes_and_flickers(self):
        points = [("091500", {"pct": 3.0}), ("092000", {"pct": 4.0}), ("092500", {"pct": 6.5})]
        states = [True, True, False, True, True, False, False, True, True, True]   # flicker, one real opening
        for i, sealed in enumerate(states):
            points.append((f"0940{i * 5:02d}", {"pct": 10.0 if sealed else 9.2, "sealed": sealed, "touched": True, "vr": 2 + i}))
        timeline = tape_timeline(tape(points))
        self.assertEqual(timeline["auction"], {"09:15": 3.0, "09:20": 4.0, "09:25": 6.5, "after_0920_change": 2.5})
        self.assertEqual(timeline["seal"]["flickers"], 1)
        self.assertEqual(timeline["seal"]["breaks"], 1)
        self.assertEqual(timeline["seal"]["first_seal"], "09:40")
        self.assertEqual(timeline["volume_ratio_peak"]["value"], 11)
        self.assertEqual(timeline["checkpoints"]["09:25"], {"pct": 6.5})
        labels = pattern_labels({"gap_pct": 6.5}, {}, {}, {}, {}, timeline)
        self.assertIn("竞价走强", labels)
        self.assertNotIn("反复炸板", labels)

    def test_closing_open_board_counts_as_a_break_and_feeds_the_review(self):
        points = [(f"1000{i * 5:02d}", {"pct": 10.0, "sealed": True}) for i in range(3)]
        points += [(f"1001{i * 5:02d}", {"pct": 8.0, "sealed": False}) for i in range(3)]
        points += [(f"1002{i * 5:02d}", {"pct": 10.0, "sealed": True}) for i in range(3)]
        points += [(f"1459{i * 5:02d}", {"pct": 7.0, "sealed": False}) for i in range(3)]
        timeline = tape_timeline(tape(points))
        self.assertEqual(timeline["seal"]["breaks"], 2)
        self.assertEqual(timeline["seal"]["episodes"], [["10:00", "10:00"], ["10:02", "10:02"]])
        review = review_stock(symbol="000001.SZ", name="测试", trade_date=date(2026, 9, 22), quote_day={}, minutes=[],
                              bars=[], limit={"touched": False, "board_opens": 1}, sector={}, sector_series=[],
                              signals=[], tape=tape(points))
        self.assertEqual(review["limit"]["board_opens"], 2)
        self.assertIn("反复炸板", review["patterns"])

    def test_peer_breadth_relation_and_empty_tape(self):
        points = []
        for i in range(60):
            minute = 570 + i
            stamp = f"{minute // 60:02d}{minute % 60:02d}00"
            points.append((stamp, {"pct": min(i, 30) * 0.2 - max(0, i - 30) * 0.1,
                                   "sec": {"g": "PCB", "gb": min(i, 20) * 0.02 - max(0, i - 20) * 0.01},
                                   "m": {"vm": 4.0 if i == 5 else 1.0, "r1": 0.8, "t": stamp[:4]},
                                   "sig": ["teacher_review:alerted:x"] if i == 10 else []}))
        timeline = tape_timeline(tape(points))
        self.assertEqual(timeline["peer_group"]["group"], "PCB")
        self.assertTrue(timeline["peer_group"]["breadth_peaked_first"])
        self.assertEqual(timeline["volume_bursts"][0]["time"], "09:35")
        self.assertEqual(timeline["signal_first_seen"], {"teacher_review:alerted": "09:40"})
        self.assertEqual(tape_timeline([])["status"], "no_tape")


if __name__ == "__main__":
    unittest.main()
