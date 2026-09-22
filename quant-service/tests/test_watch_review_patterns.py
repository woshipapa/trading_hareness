"""Label outcomes and stock/industry profiles across stored daily reviews."""

from __future__ import annotations

import unittest

from app.watch_review_patterns import next_day_outcomes, pattern_stats, stock_profiles


def review(symbol: str, day: str, labels: list[str], *, corr: float | None = None, lead: str | None = None,
           rs: float | None = None) -> dict:
    sector = {"status": "ok", "label": "元件", "corr_5m": corr, "lead": lead, "relative_strength_pct": rs} if corr is not None else {}
    return {"symbol": symbol, "name": symbol[:6], "trade_date": day, "patterns": labels, "sector": sector,
            "limit": {"touched": "封板" in labels, "sealed_at_close": "封板" in labels, "board_opens": 0}}


BARS = {"000001.SZ": {"status": "ok", "bars": [
    {"date": "20260921", "open": 10.0, "high": 10.5, "low": 9.8, "close": 10.0},
    {"date": "20260922", "open": 10.3, "high": 11.0, "low": 10.1, "close": 10.8},
    {"date": "20260923", "open": 10.8, "high": 10.9, "low": 10.2, "close": 10.4},
    {"date": "20260924", "open": 10.4, "high": 10.6, "low": 10.0, "close": 10.2},
    {"date": "20260925", "open": 10.2, "high": 10.3, "low": 9.9, "close": 10.1},
]}}


class WatchReviewPatternTests(unittest.TestCase):
    def test_next_session_outcomes_come_from_the_reviewed_close(self):
        outcomes = next_day_outcomes([review("000001.SZ", "2026-09-21", ["封板"]),
                                      review("000001.SZ", "2026-09-25", ["封板"]),       # no next session yet
                                      review("000002.SZ", "2026-09-21", [])], BARS)      # no bars
        self.assertEqual(outcomes, {("000001.SZ", "2026-09-21"): {
            "next_gap_pct": 3.0, "next_close_pct": 8.0, "next_high_pct": 10.0, "next_low_pct": 1.0, "close_3d_pct": 2.0}})

    def test_labels_and_pairs_are_counted_against_the_baseline(self):
        reviews = [review("000001.SZ", "2026-09-21", ["封板", "放量"]), review("000001.SZ", "2026-09-22", ["放量"]),
                   review("000002.SZ", "2026-09-22", ["封板", "放量"])]
        stats = pattern_stats(reviews, next_day_outcomes(reviews, BARS), min_count=2)
        self.assertEqual(stats["baseline"]["count"], 3)
        self.assertEqual(stats["labels"]["放量"]["count"], 3)
        self.assertEqual(stats["labels"]["封板"]["with_outcome"], 1)
        self.assertEqual(stats["labels"]["封板"]["next_close_pct"], {"mean": 8.0, "median": 8.0})
        self.assertEqual(list(stats["label_pairs"]), ["封板+放量"])

    def test_profiles_summarise_the_industry_relation(self):
        reviews = [review("000001.SZ", f"2026-09-2{i}", ["跟随板块"], corr=0.7, lead="stock_leads", rs=1.0) for i in range(1, 5)]
        profile = stock_profiles(reviews)["000001.SZ"]
        self.assertEqual(profile["days"], 4)
        self.assertEqual(profile["industry"], "元件")
        self.assertEqual(profile["relation"], ["常领先板块", "跟随板块", "常强于板块"])
        self.assertEqual(profile["sector"]["beat_board_rate"], 1.0)


if __name__ == "__main__":
    unittest.main()
