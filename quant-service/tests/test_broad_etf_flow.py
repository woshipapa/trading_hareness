"""The broad-ETF basket: the ratio, the source fallback, both normalizations, the stage status and storage."""

from __future__ import annotations

import asyncio
import os
import unittest
from datetime import date, timedelta
from unittest import mock

from app import broad_etf_flow as flow
from app import derived_daily_readings

CODES = [code for code, _label in flow.BASKET]


def _sessions(count: int, start: date = date(2099, 1, 1)) -> list[str]:
    return [(start + timedelta(days=i)).isoformat() for i in range(count)]


def _bars(days: list[str], amount_of=lambda code, day: 100.0, codes=CODES, source="fuyao_ths") -> dict:
    return {code: [{"trade_date": day, "ts_code": code, "amount_cny": amount_of(code, day), "volume_shares": 1.0,
                    "close": 1.0, "amount_estimated": source != "fuyao_ths", "source": source} for day in days]
            for code in codes}


class FlowReadingTests(unittest.TestCase):
    def test_ratio_is_the_session_over_the_mean_of_the_twenty_before_it(self):
        days = _sessions(22)
        bars = _bars(days, amount_of=lambda code, day: 160.0 if day == days[20] else 100.0)
        readings = flow.flow_readings(bars)
        self.assertEqual([r["trade_date"] for r in readings], days[20:], "no reading before the 20-session base")
        self.assertEqual((readings[0]["ratio"], readings[0]["surge"], readings[0]["codes"]), (1.6, True, 12))
        self.assertAlmostEqual(readings[1]["ratio"], 100 / 103, places=4)   # the base now includes the surge day
        self.assertFalse(readings[1]["surge"])

    def test_an_etf_missing_a_base_day_is_left_out_of_both_sides(self):
        days = _sessions(21)
        bars = _bars(days, amount_of=lambda code, day: 1000.0 if code == CODES[0] else 100.0)
        bars[CODES[0]] = [row for row in bars[CODES[0]] if row["trade_date"] != days[3]]
        reading = flow.flow_readings(bars)[0]
        self.assertEqual((reading["codes"], reading["ratio"]), (11, 1.0))

    def test_too_few_etfs_give_no_ratio_rather_than_a_skewed_one(self):
        days = _sessions(21)
        reading = flow.flow_readings(_bars(days, codes=CODES[:flow.MIN_CODES - 1]))[0]
        self.assertEqual((reading["ratio"], reading["surge"], reading["codes"]), (None, None, flow.MIN_CODES - 1))

    def test_estimated_turnover_is_flagged(self):
        days = _sessions(21)
        bars = _bars(days)
        bars[CODES[5]] = _bars(days, codes=[CODES[5]], source="tencent_free")[CODES[5]]
        self.assertTrue(flow.flow_readings(bars)[0]["amount_estimated"])


class SourceTests(unittest.TestCase):
    def test_fallback_serves_what_the_primary_cannot_and_both_failing_is_reported(self):
        async def primary(code, start, end):
            if code == CODES[0]:
                raise TimeoutError("slow")
            if code == CODES[1]:
                return []
            return [{"trade_date": "2099-01-01", "source": "fuyao_ths"}]

        async def fallback(code, start, end):
            if code == CODES[1]:
                raise ConnectionError("refused")
            return [{"trade_date": "2099-01-01", "source": "tencent_free"}]
        result = asyncio.run(flow.fetch_basket(date(2099, 1, 1), date(2099, 1, 2), primary=primary, fallback=fallback))
        self.assertEqual(result["bars"][CODES[0]][0]["source"], "tencent_free")
        self.assertNotIn(CODES[1], result["bars"])
        self.assertEqual(result["errors"], {CODES[1]: "primary: no rows; fallback: ConnectionError"})
        self.assertEqual(result["bars"][CODES[2]][0]["source"], "fuyao_ths")

    def test_fuyao_dates_are_beijing_midnights_and_turnover_is_exact(self):
        envelope = {"data": {"item": [
            {"date_ms": 1789920000000, "volume": 570203880, "turnover": 2624224500, "close_price": 4.608},
            {"date_ms": 1790006400000, "volume": 0, "turnover": None, "close_price": 4.6},
        ]}}
        with mock.patch("app.fuyao_provider.fetch_envelope", mock.AsyncMock(return_value=envelope)) as fetch:
            rows = asyncio.run(flow.fuyao_bars("510300.SH", date(2026, 9, 20), date(2026, 9, 22)))
        self.assertEqual(fetch.await_args.args[0], "fund_market_historical")
        self.assertEqual(rows, [{"trade_date": "2026-09-21", "ts_code": "510300.SH", "amount_cny": 2624224500.0,
                                 "volume_shares": 570203880.0, "close": 4.608, "amount_estimated": False,
                                 "source": "fuyao_ths"}])

    def test_tencent_turnover_is_estimated_from_lots_and_the_mean_price(self):
        payload = {"data": {"sh510300": {"day": [["2026-09-21", "4.586", "4.608", "4.615", "4.581", "5702039.000"]]}}}
        with mock.patch("app.datasources.http.request_json", mock.AsyncMock(return_value=payload)) as fetch:
            rows = asyncio.run(flow.tencent_bars("510300.SH", date(2026, 9, 20), date(2026, 9, 22)))
        self.assertTrue(fetch.await_args.kwargs["params"]["param"].startswith("sh510300,day,2026-09-20,2026-09-22,"))
        self.assertAlmostEqual(rows[0]["amount_cny"], 570203900 * 4.5975, delta=1)
        self.assertLess(abs(rows[0]["amount_cny"] / 2624224500 - 1), 0.002, "within 0.2% of Fuyao's exact turnover")
        self.assertTrue(rows[0]["amount_estimated"])


class RefreshTests(unittest.TestCase):
    def _run(self, days: list[str], trade_date: date) -> tuple[dict, list]:
        calls = []

        async def fetch(start, end):
            return {"bars": _bars(days), "errors": {}}

        async def run_database(function, *values, timeout_seconds):
            calls.append(function.__name__)
            return {"stored": 1, "unchanged": 0} if function is derived_daily_readings.store else 12
        return asyncio.run(flow.refresh(object(), trade_date, run_database=run_database, fetch=fetch)), calls

    def test_completed_only_when_the_session_has_a_ratio(self):
        days = _sessions(25)
        result, calls = self._run(days, date.fromisoformat(days[-1]))
        self.assertEqual((result["status"], result["latest"]["ratio"], calls), ("completed", 1.0, ["persist_bars", "store"]))
        result, _ = self._run(days[:-1], date.fromisoformat(days[-1]))
        self.assertEqual(result["status"], "blocked")


@unittest.skipUnless(os.getenv("PGHOST"), "requires the compose PostgreSQL service")
class StorageTests(unittest.TestCase):
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

    def tearDown(self) -> None:
        self.connection.rollback()
        self.connection.close()

    def test_bars_are_stored_once_and_readings_read_back(self) -> None:
        days = _sessions(21)
        bars = _bars(days, codes=CODES[:2])
        flow.persist_bars(self.database, bars)
        flow.persist_bars(self.database, bars)
        count = self.connection.execute(
            "SELECT count(*) AS n FROM quant.raw_market_observations WHERE capability=%s AND effective_at >= %s",
            (flow.BAR_CAPABILITY, derived_daily_readings.session_close(date.fromisoformat(days[0])))).fetchone()["n"]
        self.assertEqual(count, 2 * 21)
        readings = [{"trade_date": days[-1], "ratio": 1.7, "surge": True}]
        self.assertEqual(derived_daily_readings.store(self.database, flow.FLOW_CAPABILITY, readings)["stored"], 1)
        self.assertEqual(derived_daily_readings.store(self.database, flow.FLOW_CAPABILITY, readings)["unchanged"], 1)
        back = derived_daily_readings.newest(self.connection, flow.FLOW_CAPABILITY, date.fromisoformat(days[0]),
                                             date.fromisoformat(days[-1]))
        self.assertEqual(back, readings)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
