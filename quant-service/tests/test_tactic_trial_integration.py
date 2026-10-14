"""The tactic trial ledger from stored lists and bars to the board, on a real database.

The unit tests of the runtime replace the store and the settlement with mocks; this one does not. It exists because
of what such tests let through (found in review, 2026-10-14): a key the stored 09:25 table never had, Decimal
values the store cannot write, float prices multiplied by Decimal ones, and a reference price read under the wrong
name.
"""
from __future__ import annotations

import os
import unittest
from datetime import date, datetime, timedelta, timezone
from unittest import mock

from app import derived_daily_readings
from app import tactic_trial_runtime as runtime
from app.auction_open_desk import build_auction_table
from app.derived_daily_readings import session_close
from app.main import db
from app.public_market_repository import persist_timed_observations
from app.tactic_trials import TACTICS, registry_fingerprint

SYMBOLS = [f"0029{index:02d}.SZ" for index in range(1, 9)]
FIRST = date(2001, 3, 1)
STAMP = datetime(2001, 2, 1, tzinfo=timezone.utc)


def weekdays(first: date, count: int) -> list[date]:
    days, day = [], first
    while len(days) < count:
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    return days


WARM_UP = weekdays(date(2001, 2, 1), 15)     # history before the first session of the test
SESSIONS = weekdays(FIRST, 22)


def limit_up_day(stock: int, session: int) -> bool:
    return (stock + session) % 3 == 0


@unittest.skipUnless(os.getenv("PGHOST"), "requires the compose PostgreSQL service")
class TacticTrialIntegrationTests(unittest.TestCase):
    @classmethod
    def _cleanup(cls) -> None:
        with db.transaction() as connection:
            connection.execute("DELETE FROM quant.canonical_bars_daily WHERE symbol=ANY(%s)", (SYMBOLS,))
            connection.execute("DELETE FROM quant.raw_market_observations WHERE effective_at BETWEEN %s AND %s",
                               (session_close(WARM_UP[0]), session_close(SESSIONS[-1])))
            connection.execute("DELETE FROM quant.market_trade_calendar WHERE calendar_date BETWEEN %s AND %s", (WARM_UP[0], SESSIONS[-1]))
            connection.execute("DELETE FROM quant.instruments WHERE symbol=ANY(%s)", (SYMBOLS,))

    @classmethod
    def setUpClass(cls) -> None:
        cls._cleanup()
        cls.addClassCleanup(cls._cleanup)
        cls.closes: dict[tuple[str, date], float] = {}
        lists: list[dict] = []
        with db.transaction() as connection:
            for day in (*WARM_UP, *SESSIONS):
                connection.execute(
                    """INSERT INTO quant.market_trade_calendar(exchange,calendar_date,is_open,provider,available_at)
                       VALUES('SSE',%s,true,'test',%s) ON CONFLICT DO NOTHING""", (day, STAMP))
            for stock, symbol in enumerate(SYMBOLS):
                connection.execute("INSERT INTO quant.instruments(symbol,exchange,list_date,is_st) VALUES(%s,'SZ',%s,false)",
                                   (symbol, date(1999, 1, 1)))
                close = 10.0 + stock
                for index, day in enumerate((*WARM_UP, *SESSIONS)):
                    session = index - len(WARM_UP)
                    pre_close, limit_up = close, round(close * 1.10, 2)
                    sealed = session >= 0 and limit_up_day(stock, session)
                    close = limit_up if sealed else round(pre_close * 0.99, 2)
                    opening = round(pre_close * 1.02, 2)
                    connection.execute(
                        """INSERT INTO quant.canonical_bars_daily(
                               symbol,trading_date,open,high,low,close,pre_close,limit_up,limit_down,adj_factor,volume,amount,
                               is_suspended,available_at,selected_provider)
                           VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,1,100000,5000000,false,%s,'test')""",
                        (symbol, day, opening, max(opening, close), min(opening, close), close, pre_close, limit_up,
                         round(pre_close * 0.90, 2), STAMP))
                    cls.closes[symbol, day] = close
        for session, day in enumerate(SESSIONS):
            sealed = [symbol for stock, symbol in enumerate(SYMBOLS) if limit_up_day(stock, session)]
            raw = [[symbol[:6], f"股{symbol[4:6]}", 0, 0, 1760347800 + 60 * position, "机器人", 10000000, 20000000, 1, 0, 0,
                    100000000, 0, 500000000, 0, 1, 0, 0, 0, 0, 0, 0, 0] for position, symbol in enumerate(sealed)]
            lists.append({"observation_symbol": "pid:1", "rows": raw,
                          "effective_at": session_close(day).isoformat(), "available_at": session_close(day).isoformat()})
            derived_daily_readings.store(db, runtime.RISK_CAPABILITY, [{
                "trade_date": day.isoformat(), "version": "test",
                "rows": [{"symbol": symbol, "risk_rank": 10.0 * position} for position, symbol in enumerate(sealed)]}])
        persist_timed_observations(db, "longhuvip", "limit_performance_live", lists)

    def _store_auction_table(self, session: int) -> None:
        """The 09:25 table as the capture stores it: built by its own builder."""
        day, previous = SESSIONS[session], SESSIONS[session - 1]
        pool = [symbol for stock, symbol in enumerate(SYMBOLS) if limit_up_day(stock, session - 1)]
        scored = [{"symbol": symbol, "name": symbol, "previous_close": self.closes[symbol, previous], "risk_rank": 20.0, "boards": 1,
                   "sealed_from_open": False} for symbol in pool]
        snapshots = [{"symbol": symbol, "data_status": "final", "pre_close_price": self.closes[symbol, previous],
                      "open_price": round(self.closes[symbol, previous] * 1.03, 2), "auction_amount": 3000000 + position,
                      "auction_volume": 1000, "auction_unmatched": 0} for position, symbol in enumerate(pool)]
        table = build_auction_table(scored, snapshots, trade_date=day)
        self.assertEqual(len(table["rows"]), len(pool))
        derived_daily_readings.store(db, "auction_open_desk_daily", [table])

    def _stored(self, capability: str, day: date) -> dict | None:
        with db.transaction() as connection:
            return runtime._stored(connection, capability, day)

    def test_from_the_replay_to_the_board(self) -> None:
        today = SESSIONS[17]
        with mock.patch.object(runtime, "_today", return_value=today):
            self.assertEqual(runtime.evening(db, SESSIONS[15])["status"], "blocked", "nothing is built before the history replay")

            close = runtime.replay(db, "close", SESSIONS[0], SESSIONS[14], apply=True)
            self.assertEqual((close["status"], close["days"]), ("completed", 15))
            auction = runtime.replay(db, "auction", SESSIONS[1], SESSIONS[14], apply=True)
            self.assertEqual((auction["status"], auction["days"]), ("completed", 14))
            replayed = self._stored(runtime.AUCTION_CAPABILITY, SESSIONS[3])
            self.assertTrue(replayed["rows"] and all(row["reference_close"] is not None for row in replayed["rows"]),
                            "an auction row carries the previous close it is measured from")
            with db.transaction() as connection:
                digests = runtime.tactic_trial_repository.read_capability(connection, runtime.DIGEST_CAPABILITY["close"], SESSIONS[0], SESSIONS[14])
            self.assertGreaterEqual(len(digests), 6, "the pick days older than the evening window are closed by the replay")
            self.assertEqual({digest["registry"] for digest in digests}, {registry_fingerprint()})
            self.assertTrue(any(digest["resolved"] for digest in digests))

            self._store_auction_table(15)
            self.assertEqual(runtime.refresh_auction(db, SESSIONS[15])["status"], "completed")
            evening = runtime.evening(db, SESSIONS[15])
            self.assertEqual((evening["status"], evening["close"], evening["auction"]), ("completed", "completed", "already_stored"))
            self.assertEqual(runtime.summarise(db, SESSIONS[15])["unchanged"], 1, "a second run stores nothing new")

            summary = runtime.read_summary(db, SESSIONS[15])["reading"]
            self.assertEqual(summary["trade_date"], SESSIONS[15].isoformat())
            self.assertEqual(summary["day"]["sample_sizes"]["close"]["pick_day"], SESSIONS[14].isoformat(),
                             "the day section shows the newest pick day that has outcomes: the session before")
            self.assertGreater(summary["day"]["sample_sizes"]["close"]["today_rows"], 0)
            self.assertEqual(summary["stale_digest_days"], 0)
            self.assertGreater(len(summary["lines"]), 50)
            self.assertEqual(runtime.read_summary(db, SESSIONS[16])["reading"]["trade_date"], SESSIONS[15].isoformat(),
                             "before the next evening run the newest summary is still shown")

            board = runtime.read_board(db, SESSIONS[15])
            self.assertEqual((board["trade_date"], board["close"]["trade_date"]), (SESSIONS[15].isoformat(), SESSIONS[14].isoformat()))
            self.assertGreater(board["auction"]["rows"], 0)
            self.assertEqual(board["auction"]["source"], "auction_open_desk_daily")
            datetime.fromisoformat(board["auction"]["decided_at"])
            self.assertEqual(board["labels_as_of"], None, "the summary of the same evening is not known at 09:25")
            default = runtime.read_board(db)
            self.assertEqual((default["trade_date"], default["auction"], default["close"]["trade_date"]),
                             (SESSIONS[16].isoformat(), None, SESSIONS[15].isoformat()),
                             "after the evening run the board is the next session's")

            cases = next(result for result in (runtime.read_cases(db, tactic, 10) for tactic in TACTICS if tactic.slot == "close")
                         if any(day["settled"] for day in result["days"]))
            settled_day = next(day for day in cases["days"] if day["settled"])
            self.assertIsNotNone(settled_day["picks"][0]["primary"])

            # The evening of session 16 is missed; the next one builds its readings first.
            missed = runtime.evening(db, SESSIONS[17])
            self.assertEqual((missed["status"], missed["repaired"]), ("completed", {"auction": "not_ready", "close": "completed"}))
            self.assertIsNotNone(self._stored(runtime.CLOSE_CAPABILITY, SESSIONS[16]))

            with mock.patch.object(runtime, "registry_fingerprint", return_value="another-registry"):
                self.assertGreater(runtime.summarise(db, SESSIONS[17], apply=False)["summary"]["stale_digest_days"], 0,
                                   "digests of another registry are counted, not folded")
            rebuilt = runtime.rebuild_digests(db, "close", SESSIONS[0], SESSIONS[5], apply=True)
            self.assertEqual((rebuilt["days"], rebuilt["stored"]), (6, 6))

            again = runtime.replay(db, "close", SESSIONS[14], SESSIONS[17], apply=True)
            self.assertEqual(again["status"], "completed")
            self.assertFalse(self._stored(runtime.CLOSE_CAPABILITY, SESSIONS[17]).get("replayed"),
                             "a reading built live is never replaced by the replay")


if __name__ == "__main__":
    unittest.main()
