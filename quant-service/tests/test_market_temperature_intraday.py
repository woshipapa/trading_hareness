"""The intraday temperature: one minute's aggregate, the turnover profile, and agreement with the daily SQL at the close."""

from __future__ import annotations

import os
import unittest
from datetime import date, datetime, time, timedelta, timezone

from app import market_temperature_intraday as intraday
from app.derived_daily_readings import CN_TZ


def _row(symbol: str, price: float, previous: float, high: float | None = None, turnover: float = 1000.0,
         volume: float = 100.0) -> dict:
    return {"symbol": symbol, "price": price, "pct_change": (price / previous - 1) * 100, "turnover": turnover,
            "volume": volume, "raw": {"prev_price": previous, "high_price": high if high is not None else price}}


class AggregateTests(unittest.TestCase):
    def test_one_minute_counts_like_the_daily_sql(self):
        limits = {"A": (11.0, 9.0), "B": (11.0, 9.0), "C": (11.0, 9.0), "D": (13.31, 10.89)}
        prior = {"A": 1, "D": 2}
        rows = [_row("A", 11.0, 10.0), _row("B", 10.5, 10.0, high=11.0), _row("C", 9.0, 10.0),
                _row("D", 11.5, 12.1), _row("E", 5.0, 5.0), _row("F", 3.0, 3.0, volume=0)]
        totals = intraday.aggregate(rows, limits, prior)
        self.assertEqual({key: totals[key] for key in intraday.COUNT_KEYS},
                         {"stocks": 5, "with_limits": 4, "limit_up": 1, "touched": 2, "limit_down": 1, "advancers": 2,
                          "decliners": 2, "prev_sealed": 2, "promoted": 1, "max_streak": 2})
        self.assertAlmostEqual(totals["premium_pct"], ((11.0 / 10.0 - 1) + (11.5 / 12.1 - 1)) / 2 * 100)
        self.assertEqual(totals["turnover_cny"], 5000.0, "a row without volume is suspended and left out")
        self.assertFalse(totals["limits_ok"], "4 of 5 traded rows have limits: below 90%")

    def test_samples_skip_the_lunch_break(self):
        times = [moment.strftime("%H:%M") for moment in intraday.SAMPLE_TIMES]
        self.assertEqual((len(times), times[0], times[24], times[25], times[-1]), (49, "09:30", "11:30", "13:05", "15:00"))


@unittest.skipUnless(os.getenv("PGHOST"), "requires the compose PostgreSQL service")
class CloseAgreementTests(unittest.TestCase):
    DAYS = (date(2099, 3, 2), date(2099, 3, 3), date(2099, 3, 4))
    SEALER, BREAKER, FALLER = "699921.SH", "699922.SH", "699923.SH"

    def setUp(self) -> None:
        import psycopg
        from psycopg.rows import dict_row
        self.connection = psycopg.connect(
            host=os.getenv("PGHOST"), port=os.getenv("PGPORT", "5432"), dbname=os.getenv("PGDATABASE", "n8n"),
            user=os.getenv("PGUSER", "n8n"), password=os.getenv("PGPASSWORD", ""), row_factory=dict_row,
        )
        connection = self.connection

        class Database:
            def transaction(self):
                class Context:
                    def __enter__(self_inner): return connection
                    def __exit__(self_inner, *exc): return False
                return Context()
        self.database = Database()
        execute = connection.execute
        symbols = (self.SEALER, self.BREAKER, self.FALLER)
        execute("""INSERT INTO quant.instruments(symbol,exchange,name) VALUES
                     ('000001.SH','SSE','上证指数'),(%s,'SSE','封板甲'),(%s,'SSE','炸板乙'),(%s,'SSE','跌停丙')
                   ON CONFLICT(symbol) DO NOTHING""", symbols)
        for symbol in symbols:
            execute("""INSERT INTO quant.universe_membership_history(universe_key,symbol,effective_from,source)
                       VALUES('all_a',%s,'2099-01-01','test')""", (symbol,))
        stamp = datetime(2099, 3, 1, tzinfo=timezone.utc)

        def bar(symbol, day, pre, high, close, up, down):
            execute("""INSERT INTO quant.canonical_bars_daily(symbol,trading_date,open,high,low,close,pre_close,volume,amount,
                                                              limit_up,limit_down,selected_provider,available_at)
                       VALUES(%s,%s,%s,%s,%s,%s,%s,1000,%s,%s,%s,'test',%s)""",
                    (symbol, day, pre, high, min(pre, close), close, pre, close * 100, up, down, stamp))

        day1, day2, day3 = self.DAYS
        for day in self.DAYS:
            bar("000001.SH", day, 3900.0, 3900.0, 3900.0, None, None)
        # Days 1-2: the sealer seals twice (a two-board streak into day 3).
        bar(self.SEALER, day1, 10.0, 11.0, 11.0, 11.0, 9.0)
        bar(self.SEALER, day2, 11.0, 12.1, 12.1, 12.1, 9.9)
        bar(self.BREAKER, day1, 10.0, 10.2, 10.1, 11.0, 9.0)
        bar(self.BREAKER, day2, 10.1, 10.2, 10.0, 11.11, 9.09)
        bar(self.FALLER, day1, 10.0, 10.0, 10.0, 11.0, 9.0)
        bar(self.FALLER, day2, 10.0, 10.0, 10.0, 11.0, 9.0)
        # Day 3 closes: the sealer makes a third board, the breaker touched and fell back, the faller is floored.
        self.close3 = {self.SEALER: (12.1, 13.31, 13.31), self.BREAKER: (10.0, 11.0, 10.6), self.FALLER: (10.0, 10.0, 9.0)}
        limits3 = {self.SEALER: (13.31, 10.89), self.BREAKER: (11.0, 9.0), self.FALLER: (11.0, 9.0)}
        for symbol, (pre, high, close) in self.close3.items():
            bar(symbol, day3, pre, high, close, *limits3[symbol])
            execute("""INSERT INTO quant.daily_trade_limits(symbol,trading_date,limit_up,limit_down,provider,available_at)
                       VALUES(%s,%s,%s,%s,'test',%s)""", (symbol, day3, *limits3[symbol], stamp))

    def tearDown(self) -> None:
        intraday._CONTEXTS.clear()
        intraday._SAMPLES.clear()
        self.connection.rollback()
        self.connection.close()

    def _capture(self, day: date, moment: time, rows: list[dict]) -> None:
        from app import minute_cross_section
        minute_cross_section.persist_document(self.database, datetime.combine(day, moment, CN_TZ), rows)

    def test_the_close_sample_matches_the_daily_aggregate(self) -> None:
        from app.market_temperature_repository import daily_rows
        day3 = self.DAYS[2]
        morning = [_row(self.SEALER, 12.5, 12.1, high=12.6), _row(self.BREAKER, 10.9, 10.0, high=11.0),
                   _row(self.FALLER, 9.5, 10.0, high=10.0)]
        closing = [_row(symbol, close, pre, high=high, turnover=close * 100 * 1000)
                   for symbol, (pre, high, close) in self.close3.items()]
        self._capture(day3, time(10, 0, 4), morning)
        self._capture(day3, time(15, 0, 2), closing)
        series = intraday.intraday_series(self.connection, day3, now=datetime.combine(day3, time(23, 0), CN_TZ))
        self.assertEqual([item["time"] for item in series["samples"]], ["10:00", "15:00"], "missing minutes are gaps")
        self.assertEqual(series["previous_session"], self.DAYS[1].isoformat())
        at_ten = series["samples"][0]["counts"]
        self.assertEqual((at_ten["limit_up"], at_ten["touched"], at_ten["prev_sealed"], at_ten["promoted"]), (0, 1, 1, 0))
        daily = {row["trading_date"]: row for row in daily_rows(self.connection, self.DAYS[0], day3)}[day3]
        at_close = series["samples"][1]["counts"]
        for key in ("limit_up", "touched", "limit_down", "advancers", "decliners", "prev_sealed", "promoted", "max_streak"):
            self.assertEqual(at_close[key], int(daily[key]), key)
        self.assertEqual(at_close["max_streak"], 3)
        self.assertAlmostEqual(series["samples"][1]["values"]["premium"], float(daily["premium_pct"]), places=6)
        self.assertAlmostEqual(series["samples"][1]["turnover_cny"], float(daily["turnover_cny"]), delta=1)

    def test_a_stored_session_reads_back_with_its_turnover_shares(self) -> None:
        day3 = self.DAYS[2]
        self._capture(day3, time(10, 0, 4), [_row(self.SEALER, 12.5, 12.1, turnover=250.0)])
        self._capture(day3, time(15, 0, 2), [_row(self.SEALER, 13.31, 12.1, turnover=1000.0)])
        result = intraday.refresh(self.database, day3)
        self.assertEqual((result["status"], result["samples"], result["stored"]), ("blocked", 2, 1))
        stored = intraday.read(self.connection, day3)
        self.assertEqual((stored["source"], [item["share"] for item in stored["samples"]]), ("stored", [0.25, 1.0]))

    def test_the_profile_needs_five_sessions(self) -> None:
        from app import derived_daily_readings
        day = date(2099, 4, 20)
        readings = [{"trade_date": (day - timedelta(days=offset)).isoformat(),
                     "samples": [{"time": "10:00", "share": share}, {"time": "15:00", "share": 1.0}]}
                    for offset, share in zip(range(1, 6), (0.2, 0.25, 0.3, 0.35, 0.4))]
        derived_daily_readings.store(self.database, intraday.CAPABILITY, readings[:4])
        self.assertEqual(intraday.share_profile(self.connection, day), {})
        derived_daily_readings.store(self.database, intraday.CAPABILITY, readings[4:])
        self.assertEqual(intraday.share_profile(self.connection, day), {"10:00": 0.3, "15:00": 1.0})


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
