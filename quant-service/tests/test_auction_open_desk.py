from __future__ import annotations

from datetime import date, datetime, time
import unittest

from app.auction_open_desk import (
    AUCTION_POOL_MAX_SYMBOLS,
    BUCKET_STATISTICS,
    OPENS_AT_LIMIT_ONE_WORD,
    OPENS_AT_LIMIT_REOPENED,
    VERSION,
    accepts_snapshot_row,
    auction_timetable,
    build_auction_pool,
    gap_bucket,
    next_auction_tick,
    open_gap,
    opens_at_limit,
    realised_bucket,
    build_auction_table,
    auction_track_summary,
)


class AuctionOpenDeskRulesTests(unittest.TestCase):
    def test_timetable_contains_extra_ticks_five_second_phase_and_stop(self) -> None:
        ticks = auction_timetable(date(2026, 10, 12))
        times = {tick.timetz().replace(tzinfo=None) for tick in ticks}
        self.assertEqual(ticks[0].timetz().replace(tzinfo=None), time(9, 15, 5))
        for expected in (time(9, 19, 50), time(9, 20, 5), time(9, 24, 55)):
            self.assertIn(expected, times)
        self.assertEqual([tick.time() for tick in ticks if time(9, 25, 5) <= tick.time()][1], time(9, 25, 10))
        self.assertEqual(ticks[-1].time(), time(9, 26))
        self.assertTrue(all(tick.tzinfo is not None for tick in ticks))

    def test_next_tick_is_strict_and_stops_after_last_tick(self) -> None:
        tick = datetime(2026, 10, 12, 9, 19, 49, tzinfo=auction_timetable(date(2026, 10, 12))[0].tzinfo)
        self.assertEqual(next_auction_tick(tick).time(), time(9, 19, 50))
        self.assertEqual(next_auction_tick(datetime(2026, 10, 12, 9, 26, tzinfo=tick.tzinfo)), None)

    def test_pool_preserves_source_order_deduplicates_and_caps(self) -> None:
        pool = build_auction_pool(["a", "b"], ["b", "c"], ["a", "d"], cap=3)
        self.assertEqual(pool, ["a", "b", "c"])
        self.assertEqual(build_auction_pool([], [], [], cap=AUCTION_POOL_MAX_SYMBOLS), [])

    def test_gap_and_bucket_edges(self) -> None:
        self.assertAlmostEqual(open_gap(105, 100), 0.05)
        edges = [
            (-0.050001, "below_-5%"), (-0.05, "-5..-2%"), (-0.02, "-2..0%"),
            (0, "0..2%"), (0.02, "2..5%"), (0.05, "5..9%"), (0.09, "9%+_not_at_limit"),
        ]
        for gap, bucket in edges:
            self.assertEqual(gap_bucket(gap), bucket)
        self.assertEqual(gap_bucket(0.20, opened_at_limit=True), "opens_at_limit")
        self.assertEqual(len(BUCKET_STATISTICS), 8)
        self.assertEqual(VERSION, "auction-desk-v1")

    def test_open_limit_and_snapshot_acceptance_boundaries(self) -> None:
        self.assertTrue(opens_at_limit(10.0, 10.005))
        self.assertFalse(opens_at_limit(9.994, 10.0))
        self.assertTrue(accepts_snapshot_row({"data_status": "final", "pre_close_price": 100.1}, 100.0))
        self.assertFalse(accepts_snapshot_row({"data_status": "pending", "pre_close_price": 100.0}, 100.0))
        self.assertFalse(accepts_snapshot_row({"data_status": "final", "pre_close_price": 100.2}, 100.0))
        self.assertFalse(accepts_snapshot_row({"data_status": "final"}, 100.0))
        self.assertFalse(accepts_snapshot_row({"pre_close_price": 100.0}, 100.0))
        self.assertFalse(accepts_snapshot_row({"data_status": "final", "pre_close_price": 100.0}, float("nan")))
        self.assertFalse(accepts_snapshot_row({"data_status": "final", "pre_close_price": float("nan")}, 100.0))

    def test_labels_do_not_claim_a_close_direction_inside_two_standard_errors(self) -> None:
        for stats in BUCKET_STATISTICS.values():
            if abs(stats["open_to_close_mean"]) < 2 * stats["open_to_close_se"]:
                self.assertNotRegex(stats["label"], r"higher at the close|lower at the close")

    def test_table_accepts_final_rows_and_falls_back_to_auction_price(self) -> None:
        scored = [{"symbol": "000001.SZ", "name": "A", "boards": 1, "risk_rank": 10,
                   "sealed_from_open": True, "previous_close": 10.0},
                  {"symbol": "000002.SZ", "name": "B", "boards": 2, "risk_rank": 20,
                   "sealed_from_open": False, "previous_close": 10.0}]
        result = build_auction_table(scored, [
            {"thscode": "000001.SZ", "data_status": "final", "pre_close_price": 10,
             "open_price": "", "auction_price": 11, "auction_amount": 1, "auction_volume": 2, "auction_unmatched": 3},
            {"thscode": "000002.SZ", "data_status": "pending", "pre_close_price": 10, "open_price": 10},
        ], trade_date=date(2026, 10, 12))
        self.assertEqual(result["coverage"], {"scored": 2, "accepted": 1, "missing": 1})
        self.assertEqual(result["rows"][0]["open"], 11.0)
        self.assertEqual(result["rows"][0]["bucket"], "opens_at_limit")

    def test_table_rejects_non_positive_open_and_auction_prices(self) -> None:
        scored = [{"symbol": "000001.SZ", "previous_close": 10.0}]
        for auction_price in (0, None):
            result = build_auction_table(scored, [{"thscode": "000001.SZ", "data_status": "final",
                                                    "pre_close_price": 10, "open_price": 0,
                                                    "auction_price": auction_price}])
            self.assertEqual(result["coverage"]["accepted"], 0)

    def test_track_separates_a_board_that_stayed_one_word_from_one_that_reopened(self) -> None:
        day = date(2026, 10, 12)
        rows = [
            {"symbol": "000001.SZ", "bucket": "opens_at_limit", "open": 11.0},
            {"symbol": "000002.SZ", "bucket": "opens_at_limit", "open": 11.0},
            {"symbol": "000003.SZ", "bucket": "opens_at_limit", "open": 11.0},
            {"symbol": "000004.SZ", "bucket": "0..2%", "open": 10.1},
        ]
        bars = [
            {"symbol": "000001.SZ", "trading_date": day, "open": 11.0, "high": 11.0, "low": 11.0, "close": 11.0},
            {"symbol": "000002.SZ", "trading_date": day, "open": 11.0, "high": 11.0, "low": 10.5, "close": 10.67},
            {"symbol": "000003.SZ", "trading_date": day, "open": 11.0, "close": 10.9},
            {"symbol": "000004.SZ", "trading_date": day, "open": 10.1, "high": 10.3, "low": 10.0, "close": 10.2},
        ]
        buckets = auction_track_summary([{"trade_date": "2026-10-12", "rows": rows}], bars)["days"][0]["buckets"]
        self.assertEqual(buckets[OPENS_AT_LIMIT_ONE_WORD]["stocks"], 1)
        self.assertAlmostEqual(buckets[OPENS_AT_LIMIT_ONE_WORD]["open_to_close_mean"], 0.0)
        self.assertEqual(buckets[OPENS_AT_LIMIT_REOPENED]["stocks"], 1)
        self.assertAlmostEqual(buckets[OPENS_AT_LIMIT_REOPENED]["open_to_close_mean"], 10.67 / 11.0 - 1)
        self.assertIn("not buyable", buckets[OPENS_AT_LIMIT_ONE_WORD]["fill_rule"])
        self.assertIn("buyable at the limit", buckets[OPENS_AT_LIMIT_REOPENED]["fill_rule"])
        self.assertEqual(buckets["opens_at_limit"]["stocks"], 1, "a bar without a low cannot be told apart: it stays combined")
        self.assertNotIn("fill_rule", buckets["0..2%"])
        self.assertEqual(realised_bucket({"bucket": "opens_at_limit", "open": 11.0}, {"low": 11.0}), OPENS_AT_LIMIT_ONE_WORD)

    def test_track_summary_uses_only_matching_daily_bars(self) -> None:
        reading = {"trade_date": "2026-10-12", "rows": [{"symbol": "000001.SZ", "bucket": "0..2%", "open": 10}]}
        result = auction_track_summary([reading], [{"symbol": "000001.SZ", "trading_date": date(2026, 10, 12), "close": 9.5}])
        self.assertEqual(result["days"][0]["buckets"]["0..2%"]["stocks"], 1)
        self.assertEqual(result["days"][0]["buckets"]["0..2%"]["share_open_to_close_le_minus_5"], 1.0)
