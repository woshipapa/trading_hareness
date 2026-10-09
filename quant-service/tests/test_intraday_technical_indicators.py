import json
import unittest
from pathlib import Path

from app.intraday_technical_indicators import (
    advance_kdj,
    advance_macd,
    kdj_seed,
    kdj_window_bounds,
    macd_seed,
    realtime_indicators,
)


# Verbatim ``stk_factor_pro`` output for 600176.SH, unadjusted basis.  Seeding
# from one session and advancing with the next session's real close has to
# reproduce the vendor's own published values for that next session; that is
# what makes these fixtures worth more than synthetic numbers.
FACTOR_20260915 = {
    "ts_code": "600176.SH", "trade_date": "20260915", "close": "47.09",
    "expma_12_bfq": "43.69487", "macd_dif_bfq": "0.458", "macd_dea_bfq": "-0.16", "macd_bfq": "1.235",
    "kdj_k_bfq": "72.77585", "kdj_d_bfq": "62.04631", "kdj_bfq": "94.23492",
}
# The same session on the front-adjusted basis, used only to prove the reader
# follows the requested basis rather than defaulting to the unadjusted column.
FACTOR_20260915_QFQ = {**FACTOR_20260915, "expma_12_qfq": "40.0", "macd_dif_qfq": "0.4", "macd_dea_qfq": "-0.1",
                       "kdj_k_qfq": "70.0", "kdj_d_qfq": "60.0"}

# 2026-09-16 as published: close 47.39, session high 48.07, low 45.60.
NEXT_CLOSE = 47.39
NEXT_SESSION_HIGH = 48.07
NEXT_SESSION_LOW = 45.60
PUBLISHED_20260916 = {"dif": 0.719, "dea": 0.016, "macd": 1.406,
                      "k": 77.31027, "d": 67.1343, "j": 97.66222}

# The eight completed sessions before 2026-09-16, oldest first.
PRIOR_SESSIONS = [
    {"trading_date": "20260904", "high": 43.30, "low": 39.78},
    {"trading_date": "20260907", "high": 42.05, "low": 40.30},
    {"trading_date": "20260908", "high": 42.89, "low": 41.11},
    {"trading_date": "20260909", "high": 43.45, "low": 41.73},
    {"trading_date": "20260910", "high": 44.55, "low": 42.00},
    {"trading_date": "20260911", "high": 44.97, "low": 41.50},
    {"trading_date": "20260914", "high": 46.16, "low": 42.78},
    {"trading_date": "20260915", "high": 48.59, "low": 45.19},
]


class MacdSeedTests(unittest.TestCase):
    def test_ema_slow_is_recovered_from_expma_and_dif(self):
        seed = macd_seed(FACTOR_20260915)
        # EMA26 is never published; DIF = EMA12 - EMA26 is what recovers it.
        self.assertAlmostEqual(seed["ema_fast"], 43.69487, places=5)
        self.assertAlmostEqual(seed["ema_slow"], 43.69487 - 0.458, places=5)
        self.assertEqual(seed["trade_date"], "20260915")
        self.assertEqual(seed["basis"], "bfq")

    def test_a_requested_basis_reads_that_basis(self):
        seed = macd_seed(FACTOR_20260915_QFQ, basis="qfq")
        self.assertAlmostEqual(seed["ema_fast"], 40.0, places=5)
        self.assertAlmostEqual(seed["ema_slow"], 40.0 - 0.4, places=5)

    def test_a_missing_factor_column_yields_no_seed(self):
        self.assertIsNone(macd_seed({**FACTOR_20260915, "macd_dif_bfq": None}))

    def test_an_unregistered_basis_is_rejected(self):
        with self.assertRaises(ValueError):
            macd_seed(FACTOR_20260915, basis="raw")


class MacdAdvanceTests(unittest.TestCase):
    def test_one_step_reproduces_the_vendors_next_session(self):
        advanced = advance_macd(macd_seed(FACTOR_20260915), NEXT_CLOSE)
        # Seeding EMA26 through a three-decimal DIF is the only error source.
        self.assertAlmostEqual(advanced["dif"], PUBLISHED_20260916["dif"], delta=0.001)
        self.assertAlmostEqual(advanced["dea"], PUBLISHED_20260916["dea"], delta=0.001)
        self.assertAlmostEqual(advanced["macd"], PUBLISHED_20260916["macd"], delta=0.001)

    def test_the_advanced_fast_ema_matches_the_published_expma(self):
        advanced = advance_macd(macd_seed(FACTOR_20260915), NEXT_CLOSE)
        self.assertAlmostEqual(advanced["ema_fast"], 44.26335, places=4)

    def test_the_seed_session_is_carried_for_staleness_checks(self):
        advanced = advance_macd(macd_seed(FACTOR_20260915), NEXT_CLOSE)
        self.assertEqual(advanced["seed_trade_date"], "20260915")
        self.assertEqual(advanced["basis"], "bfq")

    def test_a_non_numeric_price_yields_nothing(self):
        self.assertIsNone(advance_macd(macd_seed(FACTOR_20260915), "--"))

    def test_no_seed_yields_nothing(self):
        self.assertIsNone(advance_macd(None, NEXT_CLOSE))


class KdjWindowTests(unittest.TestCase):
    def test_the_live_session_is_merged_into_eight_priors(self):
        bounds = kdj_window_bounds(PRIOR_SESSIONS, session_high=NEXT_SESSION_HIGH, session_low=NEXT_SESSION_LOW)
        self.assertEqual(bounds, (48.59, 39.78))

    def test_a_ninth_prior_session_is_dropped_rather_than_widening_the_window(self):
        # Nine completed bars plus a live tick would span ten sessions; the
        # oldest must fall out or RSV reads against a range it does not own.
        stale = [{"trading_date": "20260903", "high": 99.0, "low": 1.0}, *PRIOR_SESSIONS]
        self.assertEqual(
            kdj_window_bounds(stale, session_high=NEXT_SESSION_HIGH, session_low=NEXT_SESSION_LOW),
            (48.59, 39.78),
        )

    def test_a_live_extreme_widens_the_window(self):
        bounds = kdj_window_bounds(PRIOR_SESSIONS, session_high=50.0, session_low=39.0)
        self.assertEqual(bounds, (50.0, 39.0))

    def test_too_few_prior_sessions_yield_no_window(self):
        self.assertIsNone(
            kdj_window_bounds(PRIOR_SESSIONS[:5], session_high=NEXT_SESSION_HIGH, session_low=NEXT_SESSION_LOW)
        )

    def test_an_unusable_prior_bar_yields_no_window(self):
        broken = [{**PRIOR_SESSIONS[0], "low": None}, *PRIOR_SESSIONS[1:]]
        self.assertIsNone(
            kdj_window_bounds(broken, session_high=NEXT_SESSION_HIGH, session_low=NEXT_SESSION_LOW)
        )


class KdjAdvanceTests(unittest.TestCase):
    def test_one_step_reproduces_the_vendors_next_session(self):
        advanced = advance_kdj(kdj_seed(FACTOR_20260915), price=NEXT_CLOSE, window_high=48.59, window_low=39.78)
        # K and D carry exactly, so this reproduces the vendor to five decimals.
        self.assertAlmostEqual(advanced["k"], PUBLISHED_20260916["k"], places=4)
        self.assertAlmostEqual(advanced["d"], PUBLISHED_20260916["d"], places=4)
        self.assertAlmostEqual(advanced["j"], PUBLISHED_20260916["j"], places=4)

    def test_rsv_uses_the_window_that_includes_the_live_session(self):
        advanced = advance_kdj(kdj_seed(FACTOR_20260915), price=NEXT_CLOSE, window_high=48.59, window_low=39.78)
        self.assertAlmostEqual(advanced["rsv"], (47.39 - 39.78) / (48.59 - 39.78) * 100, places=4)

    def test_a_flat_window_stays_unavailable_rather_than_inventing_a_reading(self):
        self.assertIsNone(
            advance_kdj(kdj_seed(FACTOR_20260915), price=10.0, window_high=10.0, window_low=10.0)
        )

    def test_a_missing_carried_value_yields_nothing(self):
        self.assertIsNone(kdj_seed({**FACTOR_20260915, "kdj_d_bfq": None}))


class RealtimeIndicatorsTests(unittest.TestCase):
    def _call(self, **overrides):
        payload = {
            "factor_row": FACTOR_20260915, "price": NEXT_CLOSE, "prior_sessions": PRIOR_SESSIONS,
            "session_high": NEXT_SESSION_HIGH, "session_low": NEXT_SESSION_LOW,
        }
        payload.update(overrides)
        factor_row = payload.pop("factor_row")
        return realtime_indicators(factor_row, **payload)

    def test_a_complete_call_carries_both_indicators(self):
        result = self._call()
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["degraded"], [])
        self.assertAlmostEqual(result["macd"]["macd"], PUBLISHED_20260916["macd"], delta=0.001)
        self.assertAlmostEqual(result["kdj"]["j"], PUBLISHED_20260916["j"], places=4)
        self.assertEqual(result["seed_trade_date"], "20260915")
        self.assertEqual(result["live_effect"], "none")

    def test_a_short_window_degrades_kdj_without_suppressing_macd(self):
        # MACD needs no window at all, so a watchlist symbol with only a few
        # retained bars must still get its histogram.
        result = self._call(prior_sessions=PRIOR_SESSIONS[-3:])
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["degraded"], ["kdj"])
        self.assertIsNotNone(result["macd"])
        self.assertIsNone(result["kdj"])

    def test_a_seed_older_than_the_previous_session_is_stale_not_advanced(self):
        # 2026-10-09: the newest stk_factor_pro rows were from 2026-09-17.
        later = [*PRIOR_SESSIONS[1:], {"trading_date": "20260916", "high": 48.0, "low": 46.5}]
        result = self._call(prior_sessions=later)
        self.assertEqual(result["status"], "seed_stale")
        self.assertIn("20260915", result["reason"])
        self.assertIn("20260916", result["reason"])
        self.assertIsNone(result["macd"])

    def test_without_any_prior_session_the_seed_cannot_be_trusted(self):
        self.assertEqual(self._call(prior_sessions=[])["status"], "seed_stale")

    def test_no_factor_row_reports_the_missing_seed(self):
        self.assertEqual(self._call(factor_row=None)["status"], "seed_unavailable")

    def test_a_non_numeric_price_reports_the_price(self):
        self.assertEqual(self._call(price=None)["status"], "price_unavailable")

    def test_an_unregistered_basis_is_rejected(self):
        with self.assertRaises(ValueError):
            self._call(basis="raw")


class PayloadShapeTests(unittest.TestCase):
    """Every status answers with the same keys, so consumers need no guard."""

    KEYS = {"status", "reason", "basis", "seed_trade_date", "price", "macd", "kdj", "degraded", "live_effect"}

    def test_a_completed_reading_carries_the_declared_keys(self):
        from app.intraday_technical_indicators import realtime_indicators

        result = realtime_indicators(
            FACTOR_20260915, price=47.15, prior_sessions=PRIOR_SESSIONS,
            session_high=47.5, session_low=46.0)
        self.assertEqual(set(result), self.KEYS)
        self.assertEqual(result["status"], "completed")
        self.assertIsNone(result["reason"])

    def test_a_missing_seed_carries_the_same_keys(self):
        from app.intraday_technical_indicators import realtime_indicators

        result = realtime_indicators(None, price=47.15)
        self.assertEqual(set(result), self.KEYS)
        self.assertEqual(result["degraded"], ["macd", "kdj"])
        self.assertIsNotNone(result["reason"])

    def test_a_missing_price_carries_the_same_keys(self):
        from app.intraday_technical_indicators import realtime_indicators

        result = realtime_indicators(FACTOR_20260915, price=None)
        self.assertEqual(set(result), self.KEYS)
        self.assertEqual(result["status"], "price_unavailable")
        self.assertEqual(result["degraded"], ["macd", "kdj"])

    def test_a_row_carrying_no_factor_reports_why(self):
        from app.intraday_technical_indicators import realtime_indicators

        # The gateway's VCP payload reaches here whenever it is the only row.
        result = realtime_indicators({"ts_code": "600176.SH", "vcp_score": 60.0}, price=47.15)
        self.assertEqual(set(result), self.KEYS)
        self.assertEqual(result["status"], "seed_unavailable")
        self.assertIn("no usable factor", result["reason"])


FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "600176_daily_bfq_to_20260915.json").read_text(encoding="utf-8"))
BARS_TO_20260915 = FIXTURE["bars"]


class _BarConnection:
    """Replays daily bars for the basket read and records each query."""

    def __init__(self, bars_by_symbol):
        self.bars_by_symbol = bars_by_symbol
        self.calls = []

    def execute(self, statement, values):
        self.calls.append((" ".join(statement.split()), values))
        rows = [{"symbol": symbol, **bar} for symbol in values[0] for bar in self.bars_by_symbol.get(symbol, [])]
        return type("Result", (), {"fetchall": lambda _self: rows})()


class ComputedSeedTests(unittest.TestCase):
    def test_the_seed_from_250_unadjusted_bars_is_the_vendor_s_published_row(self):
        from app.intraday_technical_indicators import computed_seed

        seed = computed_seed(BARS_TO_20260915)
        self.assertEqual(seed["trade_date"], "20260915")
        self.assertAlmostEqual(seed["expma_12_bfq"], float(FACTOR_20260915["expma_12_bfq"]), places=5)
        self.assertAlmostEqual(seed["kdj_k_bfq"], float(FACTOR_20260915["kdj_k_bfq"]), places=5)
        self.assertAlmostEqual(seed["kdj_d_bfq"], float(FACTOR_20260915["kdj_d_bfq"]), places=5)
        # The vendor prints DIF and DEA to three decimals.
        self.assertAlmostEqual(seed["macd_dif_bfq"], float(FACTOR_20260915["macd_dif_bfq"]), delta=0.0005)
        self.assertAlmostEqual(seed["macd_dea_bfq"], float(FACTOR_20260915["macd_dea_bfq"]), delta=0.0005)
        self.assertEqual(seed["seed_sessions"], 250)

    def test_one_more_bar_is_the_same_as_advancing_the_seed_one_step(self):
        from app.intraday_technical_indicators import (
            advance_kdj, advance_macd, computed_seed, kdj_seed, kdj_window_bounds, macd_seed,
        )

        earlier, last = BARS_TO_20260915[:-1], BARS_TO_20260915[-1]
        seed = computed_seed(earlier)
        macd = advance_macd(macd_seed(seed), last["close"])
        bounds = kdj_window_bounds(earlier, session_high=last["high"], session_low=last["low"])
        kdj = advance_kdj(kdj_seed(seed), price=last["close"], window_high=bounds[0], window_low=bounds[1])
        direct = computed_seed(BARS_TO_20260915)
        self.assertAlmostEqual(macd["ema_fast"], direct["expma_12_bfq"], places=3)
        self.assertAlmostEqual(macd["dif"], direct["macd_dif_bfq"], places=3)
        self.assertAlmostEqual(kdj["k"], direct["kdj_k_bfq"], places=3)
        self.assertAlmostEqual(kdj["d"], direct["kdj_d_bfq"], places=3)

    def test_too_little_history_an_adjusted_basis_or_a_bar_without_prices_gives_no_seed(self):
        from app.intraday_technical_indicators import MIN_SEED_SESSIONS, computed_seed

        self.assertIsNone(computed_seed(BARS_TO_20260915[-(MIN_SEED_SESSIONS - 1):]))
        self.assertIsNone(computed_seed(BARS_TO_20260915, basis="qfq"))
        self.assertIsNone(computed_seed([*BARS_TO_20260915[:-1], {**BARS_TO_20260915[-1], "close": None}]))


class BasketReadingTests(unittest.TestCase):
    """The live scan reads the whole basket at once, and a session's seed only once."""

    QUOTE = {"price": NEXT_CLOSE, "session_high": NEXT_SESSION_HIGH, "session_low": NEXT_SESSION_LOW}

    def setUp(self):
        from app import intraday_technical_indicators

        intraday_technical_indicators._SESSION_BARS.clear()
        self.addCleanup(intraday_technical_indicators._SESSION_BARS.clear)

    def test_the_next_session_s_live_reading_matches_what_the_vendor_published(self):
        from datetime import date

        from app.intraday_technical_indicators import realtime_indicators_by_symbol

        connection = _BarConnection({"600176.SH": BARS_TO_20260915})
        reading = realtime_indicators_by_symbol({"600176.SH": self.QUOTE}, connection,
                                                trading_date=date(2026, 9, 16))["600176.SH"]
        self.assertEqual(reading["status"], "completed")
        self.assertEqual(reading["seed_trade_date"], "20260915")
        self.assertAlmostEqual(reading["macd"]["dif"], PUBLISHED_20260916["dif"], delta=0.001)
        self.assertAlmostEqual(reading["macd"]["macd"], PUBLISHED_20260916["macd"], delta=0.002)
        self.assertAlmostEqual(reading["kdj"]["k"], PUBLISHED_20260916["k"], places=3)
        self.assertAlmostEqual(reading["kdj"]["j"], PUBLISHED_20260916["j"], places=3)

    def test_a_session_reads_the_basket_once_and_a_new_session_reads_again(self):
        from datetime import date

        from app.intraday_technical_indicators import SEED_HISTORY_SESSIONS, realtime_indicators_by_symbol

        connection = _BarConnection({"600176.SH": BARS_TO_20260915, "002015.SZ": BARS_TO_20260915})
        quotes = {"600176.SH": self.QUOTE, "002015.SZ": self.QUOTE}
        realtime_indicators_by_symbol(quotes, connection, trading_date=date(2026, 9, 16))
        realtime_indicators_by_symbol(quotes, connection, trading_date=date(2026, 9, 16))
        self.assertEqual(len(connection.calls), 1)
        statement, values = connection.calls[0]
        self.assertIn("row_number() OVER(PARTITION BY symbol ORDER BY trading_date DESC)", statement)
        self.assertEqual(values, (["002015.SZ", "600176.SH"], date(2026, 9, 16), SEED_HISTORY_SESSIONS))
        realtime_indicators_by_symbol(quotes, connection, trading_date=date(2026, 9, 17))
        self.assertEqual(len(connection.calls), 2)

    def test_a_replay_reads_only_bars_landed_by_then_and_bypasses_the_session_cache(self):
        from datetime import date, datetime, timezone

        from app.intraday_technical_indicators import realtime_indicators_by_symbol

        known_at = datetime(2026, 9, 16, 2, tzinfo=timezone.utc)
        connection = _BarConnection({"600176.SH": BARS_TO_20260915})
        for _ in range(2):
            realtime_indicators_by_symbol({"600176.SH": self.QUOTE}, connection,
                                          trading_date=date(2026, 9, 16), known_at=known_at)
        self.assertEqual(len(connection.calls), 2)
        statement, values = connection.calls[0]
        self.assertIn("available_at<=%s", statement)
        self.assertEqual(values[2], known_at)

    def test_a_symbol_without_bars_still_gets_an_entry(self):
        from datetime import date

        from app.intraday_technical_indicators import realtime_indicators_by_symbol

        # "no reading" and "not scanned" must stay distinguishable downstream.
        readings = realtime_indicators_by_symbol({"600176.SH": self.QUOTE}, _BarConnection({}),
                                                 trading_date=date(2026, 9, 16))
        self.assertEqual(readings["600176.SH"]["status"], "seed_unavailable")

    def test_an_empty_basket_reads_nothing(self):
        from datetime import date

        from app.intraday_technical_indicators import realtime_indicators_by_symbol

        connection = _BarConnection({})
        self.assertEqual(realtime_indicators_by_symbol({}, connection, trading_date=date(2026, 9, 16)), {})
        self.assertEqual(connection.calls, [])

    def test_the_single_symbol_reading_is_the_basket_reading(self):
        from datetime import date

        from app import intraday_technical_indicators as indicators

        batched = indicators.realtime_indicators_by_symbol(
            {"600176.SH": self.QUOTE}, _BarConnection({"600176.SH": BARS_TO_20260915}),
            trading_date=date(2026, 9, 16))["600176.SH"]
        indicators._SESSION_BARS.clear()
        single = indicators.symbol_realtime_indicators(
            "600176.SH", _BarConnection({"600176.SH": BARS_TO_20260915}), price=NEXT_CLOSE,
            session_high=NEXT_SESSION_HIGH, session_low=NEXT_SESSION_LOW, trading_date=date(2026, 9, 16))
        self.assertEqual(batched, single)


if __name__ == "__main__":
    unittest.main()
