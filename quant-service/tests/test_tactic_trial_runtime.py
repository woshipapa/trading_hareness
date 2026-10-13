import asyncio
import unittest
from datetime import date, datetime, timedelta
from unittest import mock

from app import tactic_trial_runtime as runtime


class Database:
    def __init__(self):
        self.stores = []

    def transaction(self):
        database = self
        class Transaction:
            def __enter__(self): return database
            def __exit__(self, *_args): return False
        return Transaction()


class TacticTrialRuntimeTests(unittest.TestCase):
    @staticmethod
    def _raw(code, sector="theme", boards=1, epoch=1760347800, close=11):
        return [code, "A", 0, 0, epoch, sector, 10000000, 20000000, 1, 0, 0,
                100000000, 0, 500000000, 0, boards, 0, 0, 0, 0, 0, 0, 0]

    @staticmethod
    def _bar(day, symbol="000001.SZ", close=11, limit_up=11):
        return {"symbol": symbol, "trading_date": day, "open": 10, "high": close,
                "low": 10, "close": close, "pre_close": 10, "amount": 100000,
                "volume": 1000, "limit_up": limit_up, "limit_down": 9,
                "adj_factor": 1}

    def test_refresh_close_decodes_bare_codes_and_stores_once(self):
        database = Database()
        day = date(2026, 10, 13)
        raw = ["000001", "A", 0, 0, 1760347800, "theme", 10000000, 20000000, 1, 0, 0,
               100000000, 0, 500000000, 0, 1, 0, 0, 0, 0, 0, 0, 0]
        bar = {"symbol": "000001.SZ", "open": 10, "high": 11, "low": 10,
               "close": 11, "pre_close": 10, "amount": 100000, "limit_up": 11,
               "adj_factor": 1, "volume": 1000}
        lists = {"days": [{"trade_date": day, "lists": {"pid:1": [raw], "pid:2": [], "pid:3": [], "pid:4": [], "pid:5": []}}]}
        risk = [{"trade_date": day.isoformat(), "rows": [{"symbol": "000001.SZ", "risk_rank": 1}]}]
        with mock.patch.object(runtime.tactic_trial_repository, "read_capability", side_effect=[[], risk, []]), \
             mock.patch.object(runtime.tactic_trial_repository, "read_limit_lists", return_value=lists), \
             mock.patch.object(runtime.tactic_trial_repository, "read_day_bars", return_value=[bar]), \
             mock.patch.object(runtime.tactic_trial_repository, "read_history_windows", return_value=[{
                 "symbol": "000001.SZ", "pick_day": day, "trading_date": day, "close": 11,
                 "high": 11, "volume": 1000, "amount": 100000, "adj_factor": 1}]), \
             mock.patch.object(runtime.tactic_trial_repository, "read_market_turnover", return_value=[{"trading_date": day, "market_turnover": 1}]), \
             mock.patch.object(runtime.tactic_trial_repository, "read_open_sessions", return_value=[]), \
             mock.patch.object(runtime.derived_daily_readings, "store", return_value={"stored": 1, "unchanged": 0}) as store:
            result = runtime.refresh_close(database, day)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["rows"], 1)
        store.assert_called_once()

    def test_refresh_close_blocks_without_inputs(self):
        database = Database()
        day = date(2026, 10, 13)
        with mock.patch.object(runtime.tactic_trial_repository, "read_capability", side_effect=[[], []]), \
             mock.patch.object(runtime.tactic_trial_repository, "read_limit_lists", return_value={"days": []}):
            self.assertEqual(runtime.refresh_close(database, day, apply=False)["status"], "blocked")

        existing = {"trade_date": day.isoformat(), "version": runtime.VERSION, "rows": [{"symbol": "A"}]}
        with mock.patch.object(runtime.tactic_trial_repository, "read_capability", return_value=[existing]):
            result = runtime.refresh_close(database, day, apply=False)
        self.assertEqual(result["status"], "already_stored")
        self.assertTrue(result["research_only"])

    def test_refresh_auction_builds_unknown_prev_row(self):
        database = Database()
        day = date(2026, 10, 13)
        desk = {"trade_date": day.isoformat(), "rows": [{
            "symbol": "000001.SZ", "name": "A", "open": 10.5, "gap": 0.05,
            "opens_at_limit": False, "bucket": "3-5", "boards": 2, "amount": 100,
        }], "scheduled_at": "2026-10-13T09:25:00+08:00"}
        with mock.patch.object(runtime.tactic_trial_repository, "read_capability", side_effect=[[], [desk], []]), \
             mock.patch.object(runtime.tactic_trial_repository, "read_open_sessions", return_value=[]), \
             mock.patch.object(runtime.derived_daily_readings, "store", return_value={"stored": 1, "unchanged": 0}) as store:
            result = runtime.refresh_auction(database, day)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["rows"], 1)
        self.assertEqual(store.call_args.args[2][0]["decided_at"], desk["scheduled_at"])

    def test_settle_splits_prior_closes_from_pick_path(self):
        reading = {"trade_date": "2026-10-13", "slot": "close", "rows": [{"symbol": "000001.SZ"}]}
        bars = [
            {"symbol": "000001.SZ", "pick_day": date(2026, 10, 13), "trading_date": date(2026, 10, 10), "close": 9, "adj_factor": 2},
            {"symbol": "000001.SZ", "pick_day": date(2026, 10, 13), "trading_date": date(2026, 10, 13), "close": 10, "adj_factor": 1},
            {"symbol": "000001.SZ", "pick_day": date(2026, 10, 13), "trading_date": date(2026, 10, 14), "close": 11, "adj_factor": 1},
        ]
        outcome = {"entries": {"held": {"close": {"status": "settled"}}, "buy_open": {"close": {"status": "no_trade"}}}}
        with mock.patch.object(runtime, "evaluate_pick", return_value=outcome) as evaluate:
            document = runtime._settle_reading(reading, {"000001.SZ": bars})
        evaluate.assert_called_once_with("000001.SZ", "close", bars[1:], None, [18.0])
        self.assertTrue(document["complete"])

    def test_replay_keeps_missing_session_slots_in_previous_roles(self):
        days = [date(2026, 1, 5) + timedelta(days=index) for index in range(4)]
        list_days = days[:2] + days[3:]
        lists = {"days": [{"trade_date": day, "lists": {"pid:1": [self._raw("000001", boards=1, epoch=1760347800 + index * 60)],
            "pid:2": [], "pid:3": [], "pid:4": [], "pid:5": []}} for index, day in enumerate(list_days)]}
        risks = [{"trade_date": day.isoformat(), "rows": [{"symbol": "000001.SZ", "risk_rank": 1}]} for day in list_days]
        bars = [self._bar(day) for day in list_days]
        windows = [{"symbol": "000001.SZ", "pick_day": day, "trading_date": day, "close": 11,
                    "high": 11, "volume": 1000, "amount": 100000, "adj_factor": 1} for day in list_days]
        previous = []
        built_readings = []
        real_builder = runtime._build_close_reading

        def build(*args, **kwargs):
            previous.append(args[6])
            reading = real_builder(*args, **kwargs)
            built_readings.append(reading)
            return reading

        def capability(_connection, capability, _first, _last):
            return risks if capability == runtime.RISK_CAPABILITY else []

        with mock.patch.object(runtime.tactic_trial_repository, "read_open_sessions", return_value=days), \
             mock.patch.object(runtime.tactic_trial_repository, "read_limit_lists", return_value=lists), \
             mock.patch.object(runtime.tactic_trial_repository, "read_capability", side_effect=capability), \
             mock.patch.object(runtime.tactic_trial_repository, "read_day_bars", return_value=bars), \
             mock.patch.object(runtime.tactic_trial_repository, "read_history_windows", return_value=windows), \
             mock.patch.object(runtime.tactic_trial_repository, "read_market_turnover", return_value=[{"trading_date": day, "market_turnover": 1} for day in days]), \
             mock.patch.object(runtime.tactic_trial_repository, "read_exit_bars", return_value=[]), \
             mock.patch.object(runtime, "_build_close_reading", side_effect=build), \
             mock.patch.object(runtime.derived_daily_readings, "store"):
            result = runtime.replay(Database(), "close", days[0], days[-1])
        self.assertEqual(result["days"], 3)
        self.assertEqual(len(previous), 3)
        self.assertIsNone(previous[-1][0])
        self.assertIn("rebound_board", built_readings[-1]["rows"][0]["unknown"])

    def test_replay_history_feature_has_no_look_ahead(self):
        day1, day2 = date(2026, 2, 2), date(2026, 2, 3)
        lists = {"days": [{"trade_date": day, "lists": {"pid:1": [self._raw("000001")], "pid:2": [], "pid:3": [], "pid:4": [], "pid:5": []}} for day in (day1, day2)]}
        risks = [{"trade_date": day.isoformat(), "rows": [{"symbol": "000001.SZ", "risk_rank": 1}]} for day in (day1, day2)]
        bars = [self._bar(day, close=11 + index) for index, day in enumerate((day1, day2))]
        windows = [
            {"symbol": "000001.SZ", "pick_day": day1, "trading_date": day1 - timedelta(days=1), "close": 10, "high": 10, "volume": 1000, "amount": 100000, "adj_factor": 1},
            {"symbol": "000001.SZ", "pick_day": day1, "trading_date": day1, "close": 11, "high": 11, "volume": 1000, "amount": 100000, "adj_factor": 1},
            {"symbol": "000001.SZ", "pick_day": day2, "trading_date": day2 - timedelta(days=1), "close": 11, "high": 11, "volume": 1000, "amount": 100000, "adj_factor": 1},
            {"symbol": "000001.SZ", "pick_day": day2, "trading_date": day2, "close": 12, "high": 12, "volume": 1000, "amount": 100000, "adj_factor": 1},
        ]

        def run(session_days):
            def capability(_connection, capability, _first, _last):
                return risks if capability == runtime.RISK_CAPABILITY else []
            captured = []
            builder = runtime._build_close_reading
            with mock.patch.object(runtime.tactic_trial_repository, "read_open_sessions", return_value=session_days), \
                 mock.patch.object(runtime.tactic_trial_repository, "read_limit_lists", return_value=lists), \
                 mock.patch.object(runtime.tactic_trial_repository, "read_capability", side_effect=capability), \
                 mock.patch.object(runtime.tactic_trial_repository, "read_day_bars", return_value=bars), \
                 mock.patch.object(runtime.tactic_trial_repository, "read_history_windows", return_value=windows), \
                 mock.patch.object(runtime.tactic_trial_repository, "read_market_turnover", return_value=[{"trading_date": day1, "market_turnover": 1}]), \
                 mock.patch.object(runtime.tactic_trial_repository, "read_exit_bars", return_value=[]), \
                 mock.patch.object(runtime, "_build_close_reading", side_effect=lambda *args, **kwargs: (captured.append(builder(*args, **kwargs)) or captured[-1])), \
                 mock.patch.object(runtime.derived_daily_readings, "store"):
                result = runtime.replay(Database(), "close", day1, session_days[-1])
            return result, captured
        with mock.patch.object(runtime, "_settle_reading", return_value={}):
            one, one_readings = run([day1])
            two, two_readings = run([day1, day2])
        self.assertEqual(one_readings[0]["rows"][0]["runup_20"], two_readings[0]["rows"][0]["runup_20"])

    def test_replay_refusal_uses_decoder_coverage(self):
        day = date(2026, 3, 3)
        lists = {"days": [{"trade_date": day, "lists": {"pid:1": [self._raw("000001", close=10)], "pid:2": [], "pid:3": [], "pid:4": [], "pid:5": []}}]}
        risks = [{"trade_date": day.isoformat(), "rows": [{"symbol": "000001.SZ", "risk_rank": 1}]}]
        with mock.patch.object(runtime.tactic_trial_repository, "read_open_sessions", return_value=[day]), \
             mock.patch.object(runtime.tactic_trial_repository, "read_limit_lists", return_value=lists), \
             mock.patch.object(runtime.tactic_trial_repository, "read_capability", side_effect=lambda _c, capability, _s, _e: risks if capability == runtime.RISK_CAPABILITY else []), \
             mock.patch.object(runtime.tactic_trial_repository, "read_day_bars", return_value=[self._bar(day, close=10)]), \
             mock.patch.object(runtime.tactic_trial_repository, "read_history_windows", return_value=[{"symbol": "000001.SZ", "pick_day": day, "trading_date": day, "close": 10, "high": 10, "volume": 1000, "amount": 100000, "adj_factor": 1}]), \
             mock.patch.object(runtime.tactic_trial_repository, "read_market_turnover", return_value=[]), \
             mock.patch.object(runtime.derived_daily_readings, "store") as store:
            result = runtime.replay(Database(), "close", day, day, apply=True)
        self.assertEqual(result["status"], "refused")
        self.assertEqual(result["years"]["2026"]["disagreement_share"], 1.0)
        store.assert_not_called()

    def test_auction_replay_uses_yesterday_close_pool(self):
        day1, day2 = date(2026, 5, 4), date(2026, 5, 5)
        previous_rows = [{"symbol": "000001.SZ", "name": "A", "close": 10, "boards": 2, "board_bucket": "2", "sector": "x",
                          "sealed_from_open": False, "flags": [], "unknown": []},
                         {"symbol": "000002.SZ", "name": "B", "close": 10, "boards": 1, "board_bucket": "1", "sector": "x",
                          "sealed_from_open": False, "flags": [], "unknown": []}]
        close = {"trade_date": day1.isoformat(), "slot": "close", "complete": True,
                 "rows": previous_rows, "day": {"flags": [], "unknown": []}}
        auction_list = [{"trade_date": day2, "rows": [{"symbol": "000001.SZ", "amount": 900, "position": 0}]}]
        bars = [self._bar(day2, "000001.SZ", close=11, limit_up=11), self._bar(day2, "000002.SZ", close=10, limit_up=11)]
        exit_bars = [dict(bar, pick_day=day2) for bar in bars]
        exit_bars += [dict(self._bar(day2 + timedelta(days=1), "000001.SZ", close=11, limit_up=12), pick_day=day2),
                      dict(self._bar(day2 + timedelta(days=1), "000002.SZ", close=10, limit_up=12), pick_day=day2)]
        complete = {"entries": {"held": {"close": {"status": "settled", "raw_return": 0.1}},
                                 "buy_open": {"close": {"status": "no_trade"}}}}
        with mock.patch.object(runtime.tactic_trial_repository, "read_open_sessions", side_effect=[[day1, day2], [day1]]), \
             mock.patch.object(runtime.tactic_trial_repository, "read_auction_lists", return_value=auction_list), \
             mock.patch.object(runtime.tactic_trial_repository, "read_capability", return_value=[close]), \
             mock.patch.object(runtime.tactic_trial_repository, "read_day_bars", return_value=bars), \
             mock.patch.object(runtime.tactic_trial_repository, "read_exit_bars", return_value=exit_bars), \
             mock.patch.object(runtime, "evaluate_pick", return_value=complete) as evaluate, \
             mock.patch.object(runtime.derived_daily_readings, "store") as store:
            result = runtime.replay(Database(), "auction", day2, day2, apply=True)
        stored = store.call_args_list[0].args[2][0]
        self.assertEqual(result["rows"], 2)
        self.assertEqual(stored["decided_at"], "2026-05-05T09:25:00+08:00")
        self.assertEqual(stored["rows"][0]["amount"], 900)
        self.assertIsNone(stored["rows"][1]["amount"])
        settled = store.call_args_list[1].args[2][0]
        self.assertTrue(settled["complete"])
        self.assertEqual(evaluate.call_args.args[3], 10)

    def test_settle_becomes_complete_on_the_next_evening(self):
        pick_day, next_day = date(2026, 4, 1), date(2026, 4, 2)
        reading = {"trade_date": pick_day.isoformat(), "slot": "close", "complete": True,
                   "rows": [{"symbol": "000001.SZ", "reference_close": 10}]}
        state = []
        incomplete = {"entries": {"held": {"close": {"status": "not_resolved"}}, "buy_open": {"close": {"status": "settled"}}}}
        complete = {"entries": {"held": {"close": {"status": "settled"}}, "buy_open": {"close": {"status": "no_trade"}}}}

        def capability(_connection, capability, _first, _last):
            if capability == runtime.CLOSE_CAPABILITY:
                return [] if state and state[-1]["complete"] else [reading]
            if capability == runtime.SETTLED_CAPABILITY["close"]:
                return list(state)
            return []

        bars = [{"symbol": "000001.SZ", "pick_day": pick_day, "trading_date": pick_day, "close": 10},
                {"symbol": "000001.SZ", "pick_day": pick_day, "trading_date": next_day, "close": 11}]
        stored_under = []

        def store(_database, capability, documents):
            stored_under.append(capability)
            if capability == runtime.SETTLED_CAPABILITY["close"]:
                state[:] = documents
            return {"stored": 1, "unchanged": 0}

        with mock.patch.object(runtime.tactic_trial_repository, "read_open_sessions", side_effect=[[pick_day], [pick_day, next_day]]), \
             mock.patch.object(runtime.tactic_trial_repository, "read_capability", side_effect=capability), \
             mock.patch.object(runtime.tactic_trial_repository, "read_exit_bars", return_value=bars), \
             mock.patch.object(runtime, "evaluate_pick", side_effect=[incomplete, complete]) as evaluate, \
             mock.patch.object(runtime.derived_daily_readings, "store", side_effect=store):
            first = runtime.settle(Database(), pick_day)
            second = runtime.settle(Database(), next_day)
        self.assertFalse(first["days"][0]["complete"])
        self.assertTrue(second["days"][0]["complete"])
        self.assertEqual(evaluate.call_args_list[1].args[2], bars)
        self.assertEqual(stored_under[:3], [runtime.SETTLED_CAPABILITY["close"], runtime.DIGEST_CAPABILITY["close"],
                                            runtime.PAIRS_CAPABILITY["close"]],
                         "each settled day is stored with its digest and its pairs, under the slot's own capabilities")

    def test_summarise_folds_digests_in_chunks_and_reads_only_the_latest_day_in_full(self):
        sessions = [date(2026, 1, 1) + timedelta(days=offset) for offset in range(130)]
        last = sessions[-1]
        reads = []

        def readings(_connection, capability, first, end):
            reads.append((capability, first, end))
            if capability == runtime.SETTLED_CAPABILITY["close"]:
                return [{"trade_date": last.isoformat(), "slot": "close", "complete": True, "rows": []}]
            if capability == runtime.CLOSE_CAPABILITY:
                return [{"trade_date": last.isoformat(), "slot": "close", "rows": [], "day": {}}]
            return []

        with mock.patch.object(runtime.tactic_trial_repository, "read_open_sessions", return_value=sessions), \
             mock.patch.object(runtime.tactic_trial_repository, "read_capability", side_effect=readings), \
             mock.patch.object(runtime.derived_daily_readings, "newest", return_value=[]) as newest, \
             mock.patch.object(runtime.derived_daily_readings, "store", return_value={"stored": 1, "unchanged": 0}) as store:
            result = runtime.summarise(Database(), last)
        digest_reads = [item for item in reads if item[0] in runtime.DIGEST_CAPABILITY.values()]
        self.assertEqual(len(digest_reads), 2 * 3, "130 sessions are three chunks of at most 60, for two slots")
        self.assertTrue(all((end - first).days < 60 for _capability, first, end in digest_reads))
        self.assertEqual([item for item in reads if item[0] == runtime.CLOSE_CAPABILITY], [(runtime.CLOSE_CAPABILITY, last, last)],
                         "the readings are read for the latest settled day only, never as history")
        self.assertFalse([item for item in reads if item[0] == runtime.AUCTION_CAPABILITY])
        self.assertLessEqual((last - newest.call_args.args[2]).days, 15, "the previous summary is looked for in the last days only")
        self.assertEqual(store.call_args.args[1], runtime.SUMMARY_CAPABILITY)
        self.assertEqual(result["status"], "completed")


class EveningTests(unittest.TestCase):
    def test_the_evening_run_is_blocked_without_the_days_inputs_and_otherwise_runs_in_order(self):
        day = date(2026, 10, 14)
        calls = []

        def step(name, result):
            def run(_database, _day, **_kwargs):
                calls.append(name)
                return result
            return run

        with mock.patch.object(runtime, "refresh_auction", step("auction", {"status": "not_ready"})), \
             mock.patch.object(runtime, "refresh_close", step("close", {"status": "blocked"})), \
             mock.patch.object(runtime, "settle", step("settle", {"days": []})), \
             mock.patch.object(runtime, "summarise", step("summarise", {"summary": {}})):
            self.assertEqual(runtime.evening(Database(), day)["status"], "blocked")
            self.assertEqual(calls, ["auction", "close"], "nothing is settled or summarised on a day without its inputs")
        calls.clear()
        with mock.patch.object(runtime, "refresh_auction", step("auction", {"status": "completed"})), \
             mock.patch.object(runtime, "refresh_close", step("close", {"status": "already_stored"})), \
             mock.patch.object(runtime, "settle", step("settle", {"days": [{"trade_date": "2026-10-13", "slot": "close", "complete": True}]})), \
             mock.patch.object(runtime, "summarise", step("summarise", {"summary": {"lines": 80}})):
            result = runtime.evening(Database(), day)
        self.assertEqual(calls, ["auction", "close", "settle", "summarise"])
        self.assertEqual((result["status"], result["close"], result["summary"]), ("completed", "already_stored", {"lines": 80}))
        self.assertTrue(result["research_only"])


class _Stop(Exception):
    pass


class AuctionLoopTests(unittest.TestCase):
    """The loop on a clock that only moves when it sleeps."""

    def _run(self, start, statuses, *, session=True, long_sleeps=1):
        clock = {"now": datetime.fromisoformat(start).replace(tzinfo=runtime.CN_TZ)}
        calls, sleeps, lines = [], [], []

        async def run_database(action, _database, day, *, timeout_seconds):
            self.assertIs(action, runtime.refresh_auction)
            self.assertEqual(timeout_seconds, 10)
            calls.append((clock["now"].strftime("%H:%M:%S"), day))
            status = statuses[min(len(calls), len(statuses)) - 1]
            if isinstance(status, Exception):
                raise status
            return {"status": status}

        async def session_open(_day):
            return session

        async def sleep(seconds):
            sleeps.append(seconds)
            if sum(item > 3600 for item in sleeps) == long_sleeps:   # the sleep to the next morning ends the test
                raise _Stop
            clock["now"] += timedelta(seconds=seconds)

        deps = runtime.TacticAuctionDependencies(database=Database(), run_database=run_database, session_open=session_open,
                                                 now=lambda: clock["now"], sleep=sleep)
        with self.assertRaises(_Stop):
            asyncio.run(runtime.run_tactic_auction_loop(deps, log=lines.append))
        return calls, sleeps, lines

    def test_it_waits_for_the_table_and_stores_once(self):
        calls, sleeps, lines = self._run("2026-10-14T08:00:00", ["not_ready", "not_ready", "completed"], long_sleeps=2)
        self.assertEqual(sleeps[0], 5105.0, "from 08:00 it sleeps to 09:25:05")
        self.assertEqual([at for at, _day in calls], ["09:25:05", "09:25:10", "09:25:15"])
        self.assertEqual(lines, ["tactic_auction trade_date=2026-10-14 status=completed"])
        self.assertEqual(sleeps[-1], 86390.0, "then to 09:25:05 of the next day")

    def test_a_table_that_never_appears_ends_the_morning_at_0945(self):
        calls, _sleeps, lines = self._run("2026-10-14T09:25:05", ["not_ready"])
        self.assertEqual(calls[-1][0], "09:45:00")
        self.assertEqual(lines, ["tactic_auction trade_date=2026-10-14 status=no_table"])

    def test_a_busy_database_is_waited_out(self):
        busy = runtime.ExecutorSaturatedError("database executor saturated")
        calls, _sleeps, lines = self._run("2026-10-14T09:25:05", [busy, TimeoutError(), "completed"])
        self.assertEqual(len(calls), 3)
        self.assertEqual(lines, ["tactic_auction trade_date=2026-10-14 status=completed"])

    def test_no_reading_on_a_closed_day_or_outside_the_window(self):
        calls, _sleeps, lines = self._run("2026-10-14T09:30:00", ["completed"], session=False)
        self.assertEqual((calls, lines), ([], ["tactic_auction trade_date=2026-10-14 status=not_a_session"]))
        calls, sleeps, lines = self._run("2026-10-17T09:30:00", ["completed"])   # a Saturday
        self.assertEqual((calls, lines), ([], []))
        calls, sleeps, lines = self._run("2026-10-14T10:00:00", ["completed"])
        self.assertEqual((calls, lines, sleeps), ([], [], [84305.0]))


if __name__ == "__main__":
    unittest.main()
