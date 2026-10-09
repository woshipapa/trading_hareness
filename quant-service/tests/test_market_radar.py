"""The market radar's bands, reverse-engineered from the vendor chart and made finer."""

from __future__ import annotations

import unittest
from datetime import date, datetime

from app.market_radar import CN_TZ, RadarState, radar_point, segment_of, session_phase

DAY = date(2026, 10, 9)


def at(hour: int, minute: int) -> datetime:
    return datetime(2026, 10, 9, hour, minute, tzinfo=CN_TZ)


def row(symbol: str, pct: float, turnover: float, price: float | None = None) -> dict:
    return {"ts_code": symbol, "pct_change": pct, "turnover": turnover, "price": price}


class SegmentAndPhaseTests(unittest.TestCase):
    def test_every_board_has_its_own_segment(self):
        self.assertEqual([segment_of(code) for code in
                          ("600519.SH", "000001.SZ", "300750.SZ", "688981.SH", "920438.BJ", "200028.SZ", "510300.SH")],
                         ["sh_main", "sz_main", "chinext", "star", "beijing", None, None])

    def test_the_auction_is_split_where_orders_stop_being_withdrawable(self):
        self.assertEqual([session_phase(at(h, m)) for h, m in ((9, 16), (9, 21), (9, 26), (10, 0), (14, 58), (12, 0))],
                         ["auction_cancellable", "auction_locked", "auction_matched", "continuous",
                          "closing_auction", "outside"])


class BandTests(unittest.TestCase):
    def test_the_three_bands_partition_the_pool_like_the_vendor_legend(self):
        state = RadarState(DAY)
        point = radar_point([row("600001.SH", 3.0, 100.0), row("600002.SH", -4.0, 250.0),
                             row("600003.SH", 0.5, 70.0)], state, observed_at=at(10, 0))
        band = point["bands"]["2"]
        self.assertEqual((band["cum_up"]["turnover"], band["cum_down"]["turnover"], band["middle"]["turnover"]),
                         (100.0, 250.0, 70.0))
        self.assertEqual(band["cum_up"]["turnover"] + band["cum_down"]["turnover"] + band["middle"]["turnover"],
                         point["pool"]["turnover"])
        self.assertEqual(point["bands"]["5"]["cum_down"]["count"], 0)

    def test_membership_only_grows_while_the_now_reading_follows_the_price(self):
        state = RadarState(DAY)
        radar_point([row("600001.SH", 2.5, 100.0)], state, observed_at=at(10, 0))
        later = radar_point([row("600001.SH", 1.0, 180.0)], state, observed_at=at(10, 1))
        band = later["bands"]["2"]
        self.assertEqual(band["cum_up"]["turnover"], 180.0, "the day's turnover so far, of a stock that touched +2%")
        self.assertEqual(band["now_up"]["turnover"], 0.0)
        self.assertEqual(band["now_middle"]["turnover"], 180.0)
        self.assertEqual(later["entered"], {}, "nothing new entered the bands")

    def test_a_stock_counts_on_the_side_it_touched_first(self):
        state = RadarState(DAY)
        radar_point([row("600001.SH", -2.2, 100.0)], state, observed_at=at(10, 0))
        point = radar_point([row("600001.SH", 3.1, 300.0)], state, observed_at=at(10, 30))
        band = point["bands"]["2"]
        self.assertEqual((band["cum_down"]["turnover"], band["cum_up"]["turnover"]), (300.0, 0.0))
        self.assertEqual(band["now_up"]["turnover"], 300.0)

    def test_segments_partition_their_own_pools(self):
        point = radar_point([row("600001.SH", 3.0, 10.0), row("300001.SZ", -6.0, 20.0), row("688001.SH", 0.1, 30.0),
                             row("920001.BJ", 9.0, 40.0)], RadarState(DAY), observed_at=at(10, 0))
        segments = point["segments"]
        self.assertEqual(segments["chinext"]["bands"]["5"]["cum_down"]["turnover"], 20.0)
        self.assertEqual(segments["star"]["bands"]["2"]["middle"]["turnover"], 30.0)
        self.assertEqual(segments["beijing"]["bands"]["5"]["now_up"]["count"], 1)
        self.assertEqual(sum(segment["pool"]["turnover"] for segment in segments.values()), point["pool"]["turnover"])

    def test_the_limit_band_uses_the_published_prices(self):
        limits = {"600001.SH": (11.0, 9.0), "600002.SH": (22.0, 18.0)}
        point = radar_point([row("600001.SH", 10.0, 50.0, price=11.0), row("600002.SH", 4.0, 60.0, price=20.8)],
                            RadarState(DAY), observed_at=at(10, 0), limits=limits)
        self.assertEqual(point["bands"]["limit"]["now_up"]["count"], 1)
        self.assertEqual(point["bands"]["limit"]["middle"]["turnover"], 60.0)
        self.assertNotIn("limit", radar_point([row("600001.SH", 1.0, 1.0)], RadarState(DAY),
                                              observed_at=at(10, 0))["bands"])

    def test_unusable_rows_are_skipped_and_counted(self):
        point = radar_point([row("600001.SH", 1.0, 1.0), {"ts_code": "600002.SH", "pct_change": None, "turnover": 5},
                             row("510300.SH", 1.0, 9.0), row("600003.SH", 1.0, None)], RadarState(DAY),
                            observed_at=at(10, 0))
        self.assertEqual(point["skipped_rows"], 3)
        self.assertEqual(point["pool"]["count"], 1)


class AuctionTests(unittest.TestCase):
    def test_before_the_match_only_the_virtual_price_distribution_is_reported(self):
        state = RadarState(DAY)
        point = radar_point([row("600001.SH", 6.0, 0.0), row("600002.SH", -2.5, 0.0), row("600003.SH", 0.0, 0.0)],
                            state, observed_at=at(9, 18))
        self.assertEqual(point["phase"], "auction_cancellable")
        self.assertEqual(point["auction"]["up"], {"2": 1, "5": 1})
        self.assertEqual(point["auction"]["down"], {"2": 1, "5": 0})
        self.assertNotIn("bands", point)
        self.assertEqual(state.first_touch, {}, "a withdrawable virtual price enters no stock into the day")

    def test_the_0925_match_counts_and_its_turnover_is_the_auction_turnover(self):
        state = RadarState(DAY)
        point = radar_point([row("600001.SH", 4.0, 5_000_000.0), row("600002.SH", -3.0, 1_000_000.0)],
                            state, observed_at=at(9, 26))
        self.assertEqual(point["phase"], "auction_matched")
        self.assertEqual(point["bands"]["2"]["cum_up"]["turnover"], 5_000_000.0)
        self.assertEqual(point["entered"]["2"], {"up": ["600001.SH"], "down": ["600002.SH"]})


class RestoreTests(unittest.TestCase):
    def test_a_restart_rebuilt_from_stored_points_answers_like_an_uninterrupted_run(self):
        frames = [
            (at(9, 31), [row("600001.SH", 2.4, 10.0), row("600002.SH", -1.0, 10.0)]),
            (at(9, 32), [row("600001.SH", 0.2, 20.0), row("600002.SH", -5.5, 30.0)]),
            (at(9, 33), [row("600001.SH", -2.1, 40.0), row("600002.SH", 0.0, 50.0)]),
        ]
        live = RadarState(DAY)
        stored = [radar_point(rows, live, observed_at=moment) for moment, rows in frames[:2]]
        uninterrupted = radar_point(frames[2][1], live, observed_at=frames[2][0])
        restored = RadarState(DAY)
        for point in stored:
            restored.restore(point)
        self.assertEqual(radar_point(frames[2][1], restored, observed_at=frames[2][0]), uninterrupted)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
