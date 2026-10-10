"""The golden / silver finger state: the replay, volume confirmation, the source rule and the stage status."""

from __future__ import annotations

import asyncio
import unittest
from datetime import date, timedelta
from unittest import mock

from app import derived_daily_readings
from app import market_timing as timing


def _bars(closes: list[float], volumes: list[float]) -> list[dict]:
    start = date(2099, 1, 1)
    return [{"trade_date": (start + timedelta(days=i)).isoformat(), "close": c, "volume": v, "source": "fuyao_ths"}
            for i, (c, v) in enumerate(zip(closes, volumes))]


class StateTests(unittest.TestCase):
    def test_a_confirmed_rise_turns_golden_and_a_dead_cross_turns_silver(self):
        closes = [100.0] * 60 + [100.0 + 2 * i for i in range(1, 11)] + [120.0 - 3 * i for i in range(1, 16)]
        volumes = [100.0] * 60 + [300.0] * 25
        readings = timing.timing_states(_bars(closes, volumes))
        events = [(r["trade_date"], r["event"]) for r in readings if r["event"]]
        self.assertEqual([kind for _day, kind in events], ["golden", "silver"])
        golden_day = events[0][0]
        self.assertEqual(golden_day, _bars(closes, volumes)[60]["trade_date"], "MA5 crosses MA25 on the first rising day")
        self.assertEqual(readings[-1]["state"], "silver")

    def test_without_volume_the_cross_waits_for_confirmation(self):
        closes = [100.0] * 60 + [100.0 + 2 * i for i in range(1, 11)]
        quiet = [100.0] * 65 + [50.0] * 5
        loud = [100.0] * 65 + [50.0] * 3 + [900.0] * 2
        self.assertEqual({r["state"] for r in timing.timing_states(_bars(closes, quiet))}, {"silver"})
        events = [r for r in timing.timing_states(_bars(closes, loud)) if r["event"]]
        self.assertEqual((len(events), events[0]["event"], events[0]["trade_date"]),
                         (1, "golden", _bars(closes, loud)[68]["trade_date"]))

    def test_no_reading_before_the_sixty_session_volume_base(self):
        self.assertEqual(timing.timing_states(_bars([100.0] * 59, [1.0] * 59)), [])
        self.assertEqual(len(timing.timing_states(_bars([100.0] * 61, [1.0] * 61))), 2)


class SourceTests(unittest.TestCase):
    def test_one_source_serves_the_whole_window(self):
        async def broken(start, end):
            raise TimeoutError("slow")

        async def tencent(start, end):
            return [{"trade_date": "2099-01-02", "close": 1.0, "volume": 1.0, "source": "tencent_free"},
                    {"trade_date": "2099-01-01", "close": 1.0, "volume": 1.0, "source": "tencent_free"}]
        result = asyncio.run(timing.fetch_index(date(2099, 1, 1), date(2099, 1, 2), primary=broken, fallback=tencent))
        self.assertEqual(([bar["trade_date"] for bar in result["bars"]], result["errors"]),
                         (["2099-01-01", "2099-01-02"], ["broken: TimeoutError"]))

    def test_fuyao_rows_are_normalized(self):
        envelope = {"data": {"item": [{"date_ms": 1791475200000, "volume": 53514992000, "turnover": 1, "close_price": 3813.79},
                                      {"date_ms": 1791561600000, "volume": 0, "close_price": 3800.0}]}}
        with mock.patch("app.fuyao_provider.fetch_envelope", mock.AsyncMock(return_value=envelope)) as fetch:
            bars = asyncio.run(timing.fuyao_index_bars(date(2026, 10, 1), date(2026, 10, 9)))
        self.assertEqual(fetch.await_args.args[0], "ths_index_prices_historical")
        self.assertEqual(bars, [{"trade_date": "2026-10-09", "close": 3813.79, "volume": 53514992000.0, "source": "fuyao_ths"}])


class RefreshTests(unittest.TestCase):
    def test_completed_only_when_the_session_has_a_state(self):
        bars = _bars([100.0] * 70, [1.0] * 70)
        last = date.fromisoformat(bars[-1]["trade_date"])

        async def fetch(start, end):
            return {"bars": bars, "errors": []}

        async def run_database(function, *values, timeout_seconds):
            self.assertIs(function, derived_daily_readings.store)
            return {"stored": len(values[2]), "unchanged": 0}
        result = asyncio.run(timing.refresh(object(), last, run_database=run_database, fetch=fetch, keep=5))
        self.assertEqual((result["status"], result["stored"], result["latest"]["state"]), ("completed", 5, "silver"))
        later = asyncio.run(timing.refresh(object(), last + timedelta(days=1), run_database=run_database, fetch=fetch))
        self.assertEqual(later["status"], "blocked")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
