import unittest

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
        result = self._call(prior_sessions=PRIOR_SESSIONS[:3])
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["degraded"], ["kdj"])
        self.assertIsNotNone(result["macd"])
        self.assertIsNone(result["kdj"])

    def test_no_factor_row_reports_the_missing_seed(self):
        self.assertEqual(self._call(factor_row=None)["status"], "seed_unavailable")

    def test_a_non_numeric_price_reports_the_price(self):
        self.assertEqual(self._call(price=None)["status"], "price_unavailable")

    def test_an_unregistered_basis_is_rejected(self):
        with self.assertRaises(ValueError):
            self._call(basis="raw")


class _Connection:
    """Replays canned result sets and records the SQL it was handed."""

    def __init__(self, factor_rows=None, bar_rows=None):
        self.factor_rows = factor_rows if factor_rows is not None else [{"row_data": FACTOR_20260915}]
        self.bar_rows = bar_rows if bar_rows is not None else [
            {"trading_date": bar["trading_date"], "high": bar["high"], "low": bar["low"]}
            for bar in reversed(PRIOR_SESSIONS)
        ]
        self.calls = []

    def execute(self, statement, values):
        self.calls.append((" ".join(statement.split()), values))
        rows = self.factor_rows if "tushare_raw_records" in statement else self.bar_rows
        return type("Result", (), {"fetchall": lambda _self: list(rows)})()


class SeedReaderTests(unittest.TestCase):
    def test_the_seed_excludes_the_live_session(self):
        from datetime import date

        from app.intraday_technical_indicators import latest_factor_row

        connection = _Connection()
        latest_factor_row("600176.SH", connection, before_trading_date=date(2026, 9, 16))
        statement, values = connection.calls[0]
        # Seeding from the live session's own factors and then advancing with
        # that session's price would count it twice, and look plausible.
        self.assertIn("row_data->>'trade_date' < %s", statement)
        self.assertEqual(values, ("600176.SH", "20260916"))

    def test_a_replay_cutoff_hides_rows_published_later(self):
        from datetime import date, datetime, timezone

        from app.intraday_technical_indicators import latest_factor_row

        connection = _Connection()
        known_at = datetime(2026, 9, 16, 1, 0, tzinfo=timezone.utc)
        latest_factor_row("600176.SH", connection, before_trading_date=date(2026, 9, 16), known_at=known_at)
        statement, values = connection.calls[0]
        self.assertIn("available_at<=%s", statement)
        self.assertEqual(values[-1], known_at)

    def test_no_stored_factor_row_yields_no_seed(self):
        from datetime import date

        from app.intraday_technical_indicators import latest_factor_row

        self.assertIsNone(
            latest_factor_row("600176.SH", _Connection(factor_rows=[]), before_trading_date=date(2026, 9, 16))
        )

    def test_prior_bars_are_returned_oldest_first(self):
        from datetime import date

        from app.intraday_technical_indicators import prior_session_bars

        bars = prior_session_bars("600176.SH", _Connection(), before_trading_date=date(2026, 9, 16))
        self.assertEqual(bars[0]["trading_date"], "20260904")
        self.assertEqual(bars[-1]["trading_date"], "20260915")

    def test_a_composed_read_reproduces_the_vendors_next_session(self):
        from datetime import date

        from app.intraday_technical_indicators import symbol_realtime_indicators

        result = symbol_realtime_indicators(
            "600176.SH", _Connection(), price=NEXT_CLOSE, session_high=NEXT_SESSION_HIGH,
            session_low=NEXT_SESSION_LOW, trading_date=date(2026, 9, 16),
        )
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["symbol"], "600176.SH")
        self.assertAlmostEqual(result["macd"]["macd"], PUBLISHED_20260916["macd"], delta=0.001)
        self.assertAlmostEqual(result["kdj"]["j"], PUBLISHED_20260916["j"], places=4)


if __name__ == "__main__":
    unittest.main()
