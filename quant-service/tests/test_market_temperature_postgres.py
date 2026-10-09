"""The temperature's daily aggregate SQL on the real schema: fallback limits, CNY-unit rows, streaks and the money effect.

Sessions are dated 2099 and everything runs in one transaction that is rolled back.
"""

from __future__ import annotations

import os
import unittest
from datetime import date, datetime, timezone

DAY1, DAY2, DAY3 = date(2099, 3, 2), date(2099, 3, 3), date(2099, 3, 4)
SEALER, BREAKER, FALLER = "699911.SH", "699912.SH", "699913.SH"


@unittest.skipUnless(os.getenv("PGHOST"), "requires the compose PostgreSQL service")
class MarketTemperatureSqlTests(unittest.TestCase):
    def setUp(self) -> None:
        import psycopg
        from psycopg.rows import dict_row
        self.connection = psycopg.connect(
            host=os.getenv("PGHOST"), port=os.getenv("PGPORT", "5432"), dbname=os.getenv("PGDATABASE", "n8n"),
            user=os.getenv("PGUSER", "n8n"), password=os.getenv("PGPASSWORD", ""), row_factory=dict_row,
        )
        execute = self.connection.execute
        execute("""INSERT INTO quant.instruments(symbol,exchange,name) VALUES
                     ('000001.SH','SSE','上证指数'),(%s,'SSE','封板甲'),(%s,'SSE','炸板乙'),(%s,'SSE','跌停丙')
                   ON CONFLICT(symbol) DO NOTHING""", (SEALER, BREAKER, FALLER))
        for symbol in (SEALER, BREAKER, FALLER):
            execute("""INSERT INTO quant.universe_membership_history(universe_key,symbol,effective_from,source)
                       VALUES('all_a',%s,'2099-01-01','test')""", (symbol,))
        stamp = datetime(2099, 3, 1, tzinfo=timezone.utc)

        def bar(symbol, day, pre, high, close, *, up, down, volume=1000.0, amount=None):
            amount = close * volume / 10 if amount is None else amount
            execute("""INSERT INTO quant.canonical_bars_daily(symbol,trading_date,open,high,low,close,pre_close,volume,amount,
                                                              limit_up,limit_down,selected_provider,available_at)
                       VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'test',%s)""",
                    (symbol, day, pre, high, min(pre, close), close, pre, volume, amount, up, down, stamp))

        for day, close in ((DAY1, 3900.0), (DAY2, 3910.0), (DAY3, 3880.0)):
            bar("000001.SH", day, close, close, close, up=None, down=None)
        # Day 1: the sealer closes at its limit, the breaker touches it and falls back, the faller is floored.
        bar(SEALER, DAY1, 10.0, 11.0, 11.0, up=11.0, down=9.0)
        bar(BREAKER, DAY1, 10.0, 11.0, 10.5, up=11.0, down=9.0)
        bar(FALLER, DAY1, 10.0, 10.0, 9.0, up=11.0, down=9.0)
        # Day 2: the bars carry no limit prices; daily_trade_limits supplies them. The sealer advances.
        bar(SEALER, DAY2, 11.0, 12.1, 12.1, up=None, down=None)
        bar(BREAKER, DAY2, 10.5, 10.6, 10.6, up=None, down=None)
        bar(FALLER, DAY2, 9.0, 9.1, 9.1, up=None, down=None)
        for symbol, up, down in ((SEALER, 12.1, 9.9), (BREAKER, 11.55, 9.45), (FALLER, 9.9, 8.1)):
            execute("""INSERT INTO quant.daily_trade_limits(symbol,trading_date,limit_up,limit_down,provider,available_at)
                       VALUES(%s,%s,%s,%s,'test',%s)""", (symbol, DAY2, up, down, stamp))
        # Day 3: yesterday's two-board sealer falls 5%; the breaker's amount is in CNY, a thousand times too large.
        bar(SEALER, DAY3, 12.1, 12.2, 11.5, up=13.31, down=10.89)
        bar(BREAKER, DAY3, 10.6, 10.7, 10.65, up=11.66, down=9.54, amount=10.65 * 1000 / 10 * 1000)
        bar(FALLER, DAY3, 9.1, 9.2, 9.15, up=10.01, down=8.19)

    def tearDown(self) -> None:
        self.connection.rollback()
        self.connection.close()

    def test_the_daily_aggregate_on_the_real_schema(self) -> None:
        from app.market_temperature_repository import daily_rows

        rows = {row["trading_date"]: row for row in daily_rows(self.connection, DAY1, DAY3)}
        first, second, third = rows[DAY1], rows[DAY2], rows[DAY3]
        self.assertEqual((first["limit_up"], first["touched"], first["limit_down"]), (1, 2, 1))
        self.assertEqual((first["advancers"], first["decliners"]), (2, 1))
        # day 2's limits come from daily_trade_limits, so the streak and the promotion survive the gap
        self.assertTrue(second["limits_ok"])
        self.assertEqual((second["limit_up"], second["prev_sealed"], second["promoted"], second["max_streak"]), (1, 1, 1, 2))
        # day 3: the money effect is the sealer's -4.96%, and the CNY-unit amount is scaled back
        self.assertEqual((third["prev_sealed"], third["promoted"], third["max_streak"]), (1, 0, 0))
        self.assertAlmostEqual(float(third["premium_pct"]), (11.5 / 12.1 - 1) * 100, places=4)
        expected = sum(close * 1000 / 10 for close in (11.5, 10.65, 9.15)) * 1000
        self.assertAlmostEqual(float(third["turnover_cny"]), expected, places=2)
        self.assertEqual(float(third["index_close"]), 3880.0)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
