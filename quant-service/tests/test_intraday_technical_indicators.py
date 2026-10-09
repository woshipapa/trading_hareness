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


class BatchedReaderTests(unittest.TestCase):
    """The live scan reads the whole basket at once, not once per symbol."""

    def _connection(self):
        return _Connection(
            factor_rows=[
                {"symbol": "600176.SH", "row_data": FACTOR_20260915},
                {"symbol": "002015.SZ", "row_data": FACTOR_20260915},
            ],
            bar_rows=[
                {"symbol": symbol, "trading_date": bar["trading_date"], "high": bar["high"], "low": bar["low"]}
                for symbol in ("002015.SZ", "600176.SH")
                for bar in PRIOR_SESSIONS
            ],
        )

    def test_the_basket_seed_read_rejects_rows_carrying_no_seed(self):
        from datetime import date

        from app.intraday_technical_indicators import latest_factor_rows_by_symbol

        connection = self._connection()
        seeds = latest_factor_rows_by_symbol(
            ["600176.SH", "002015.SZ"], connection, before_trading_date=date(2026, 9, 16))
        statement, values = connection.calls[0]
        self.assertIn("DISTINCT ON (row_data->>'ts_code')", statement)
        self.assertIn("row_data ?| %s::text[]", statement)
        self.assertEqual(values, (["002015.SZ", "600176.SH"], "20260916", ["expma_12_bfq", "kdj_k_bfq"]))
        self.assertEqual(sorted(seeds), ["002015.SZ", "600176.SH"])

    def test_the_basket_is_read_once_rather_than_once_per_symbol(self):
        from datetime import date

        from app.intraday_technical_indicators import realtime_indicators_by_symbol

        connection = self._connection()
        quotes = {
            "600176.SH": {"price": 47.15, "session_high": 47.5, "session_low": 46.0},
            "002015.SZ": {"price": 16.09, "session_high": 16.3, "session_low": 16.07},
        }
        readings = realtime_indicators_by_symbol(quotes, connection, trading_date=date(2026, 9, 16))
        self.assertEqual(len(connection.calls), 2)
        self.assertEqual(sorted(readings), ["002015.SZ", "600176.SH"])
        for symbol, reading in readings.items():
            self.assertEqual(reading["symbol"], symbol)
            self.assertEqual(reading["status"], "completed")
            self.assertEqual(reading["degraded"], [])

    def test_the_batched_reading_matches_the_single_symbol_reading(self):
        from datetime import date

        from app.intraday_technical_indicators import (
            realtime_indicators_by_symbol, symbol_realtime_indicators,
        )

        quote = {"price": 47.15, "session_high": 47.5, "session_low": 46.0}
        # One symbol only: the fake replays its canned rows whatever the query
        # asks for, so the basket's rows must match the basket under test.
        connection = _Connection(
            factor_rows=[{"symbol": "600176.SH", "row_data": FACTOR_20260915}],
            bar_rows=[{"symbol": "600176.SH", "trading_date": bar["trading_date"],
                       "high": bar["high"], "low": bar["low"]} for bar in PRIOR_SESSIONS],
        )
        batched = realtime_indicators_by_symbol(
            {"600176.SH": quote}, connection, trading_date=date(2026, 9, 16))["600176.SH"]
        single = symbol_realtime_indicators(
            "600176.SH", _Connection(), price=quote["price"], session_high=quote["session_high"],
            session_low=quote["session_low"], trading_date=date(2026, 9, 16))
        self.assertEqual(batched, single)

    def test_a_symbol_without_a_seed_still_gets_an_entry(self):
        from datetime import date

        from app.intraday_technical_indicators import realtime_indicators_by_symbol

        # "no reading" and "not scanned" must stay distinguishable downstream.
        connection = _Connection(factor_rows=[], bar_rows=[])
        readings = realtime_indicators_by_symbol(
            {"600176.SH": {"price": 47.15, "session_high": 47.5, "session_low": 46.0}},
            connection, trading_date=date(2026, 9, 16))
        self.assertEqual(readings["600176.SH"]["status"], "seed_unavailable")

    def test_an_empty_basket_reads_nothing(self):
        from datetime import date

        from app.intraday_technical_indicators import realtime_indicators_by_symbol

        connection = _Connection()
        self.assertEqual(realtime_indicators_by_symbol({}, connection, trading_date=date(2026, 9, 16)), {})
        self.assertEqual(connection.calls, [])


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
        self.assertEqual(values, ("600176.SH", "20260916", ["expma_12_bfq", "kdj_k_bfq"]))

    def test_a_replay_cutoff_hides_rows_published_later(self):
        from datetime import date, datetime, timezone

        from app.intraday_technical_indicators import latest_factor_row

        connection = _Connection()
        known_at = datetime(2026, 9, 16, 1, 0, tzinfo=timezone.utc)
        latest_factor_row("600176.SH", connection, before_trading_date=date(2026, 9, 16), known_at=known_at)
        statement, values = connection.calls[0]
        self.assertIn("available_at<=%s", statement)
        # Order matters: the cutoff binds before the seed-key array.
        self.assertEqual(values, ("600176.SH", "20260916", known_at, ["expma_12_bfq", "kdj_k_bfq"]))

    def test_a_row_without_seed_factors_cannot_shadow_a_usable_one(self):
        from datetime import date

        from app.intraday_technical_indicators import latest_factor_row

        # The gateway has served a VCP breakout payload under this api_name.
        # Such a row is newer than the real seed and would otherwise win the
        # ORDER BY, leaving the symbol with no indicators at all.
        connection = _Connection()
        latest_factor_row("600176.SH", connection, before_trading_date=date(2026, 9, 17))
        statement, values = connection.calls[0]
        self.assertIn("row_data ?| %s::text[]", statement)
        self.assertEqual(values[-1], ["expma_12_bfq", "kdj_k_bfq"])

    def test_the_seed_keys_follow_the_requested_basis(self):
        from datetime import date

        from app.intraday_technical_indicators import latest_factor_row

        connection = _Connection()
        latest_factor_row("600176.SH", connection, before_trading_date=date(2026, 9, 17), basis="hfq")
        self.assertEqual(connection.calls[0][1][-1], ["expma_12_hfq", "kdj_k_hfq"])

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
