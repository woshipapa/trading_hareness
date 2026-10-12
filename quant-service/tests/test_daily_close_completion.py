import asyncio
import unittest
from contextlib import contextmanager
from datetime import date, datetime, timezone
from decimal import Decimal
from unittest.mock import patch

import app.daily_close_completion as completion


DAY = date(2026, 10, 12)
NOW = datetime(2026, 10, 12, 10, tzinfo=timezone.utc)
OUTCOMES = {name: [] for name in ("filled", "not_traded", "adjusted_later", "unverified", "invalid", "failed")}


def bar(symbol, *, pre_close=10, limit_up=11, limit_down=9, adj_factor=1,
        quality_status="fresh", open_price=10):
    return {"symbol": symbol, "open": open_price, "high": 11, "low": 9, "close": 10,
            "pre_close": pre_close, "limit_up": limit_up, "limit_down": limit_down,
            "adj_factor": adj_factor, "is_suspended": False, "quality_status": quality_status}


def quote(symbol, pre_close=10):
    return {"ts_code": symbol, "trade_date": "20261012", "open": 10, "high": 11,
            "low": 9, "close": 10.5, "pre_close": pre_close, "vol": 100, "amount": 1000}


class CompletionDb:
    def __init__(self, bars):
        self.bars = {symbol: dict(row) for symbol, row in bars.items()}
        self.statements = []
        self.persisted = []

    @contextmanager
    def transaction(self):
        yield self

    def execute(self, statement, params=None):
        self.statements.append((statement, params))


class Source:
    def raw_call(self, _request):
        raise AssertionError("the patched vendor runner owns this call")


class DailyCloseRulesTests(unittest.TestCase):
    def test_limit_values_cover_main_registration_and_beijing_rounding(self):
        rows = completion.limit_rows([
            {"symbol": "000001.SZ", "pre_close": "10.005", "ratios": ["0.10"]},
            {"symbol": "300001.SZ", "pre_close": "10.005", "ratios": ["0.20"]},
            {"symbol": "920001.BJ", "pre_close": "10.005", "ratios": ["0.30"]},
        ], DAY)
        self.assertEqual([(row["up_limit"], row["down_limit"]) for row in rows],
                         [("11.01", "9.00"), ("12.01", "8.00"), ("13.00", "7.01")])

    def test_limit_agreement_ignores_unusable_previous_closes(self):
        rows = [bar("000001.SZ"), {**bar("000002.SZ"), "pre_close": None}]
        for row in rows:
            row["ratios"] = [0.10]
        self.assertEqual(completion.limit_rule_agreement(rows), 1.0)

    def test_factor_carry_requires_stable_neighbours_and_no_marker(self):
        result = completion.factor_rows([
            {"symbol": "000001.SZ", "prev_date": DAY, "prev_factor": Decimal("1.2"), "next_factor": Decimal("1.2")},
            {"symbol": "000002.SZ", "prev_factor": 1, "next_factor": 2},
            {"symbol": "000003.SZ", "prev_factor": 1, "ex_rights_on_day": True},
        ], DAY)
        self.assertEqual([row["ts_code"] for row in result["rows"]], ["000001.SZ"])
        self.assertEqual(result["pending"], {"000002.SZ": "factor_changes_across_session",
                                             "000003.SZ": "ex_rights_on_session"})

    def test_vendor_timeout_scales_and_is_capped(self):
        self.assertLess(completion.request_timeout_seconds(1), completion.request_timeout_seconds(600))
        self.assertEqual(completion.request_timeout_seconds(10_000), 480)


class DailyCloseCompletionTests(unittest.TestCase):
    def run_completion(self, *, state, db=None, quotes=(), outcomes=None, errors=None, markers=None,
                       ratios=None, factors=(), source_error=None, vendor_error=None,
                       persistence_error=None, reject=(), during_vendor=None, agreement=None):
        db = db or CompletionDb({row["symbol"]: row for row in state["bars"]})
        calls = {"vendor": [], "persist": []}
        health = {"outcomes": {**OUTCOMES, **(outcomes or {})}, "errors": errors or {},
                  "ex_rights_on_day": markers or {row["ts_code"]: False for row in quotes}}

        def day_bars(_connection, _day, symbols=None):
            return [dict(row) for symbol, row in db.bars.items() if symbols is None or symbol in symbols]

        def persist_rows(_connection, api, _key, rows, _provider, _available):
            rows = list(rows)
            calls["persist"].append((api, [dict(row) for row in rows]))
            for row in rows:
                symbol = row["ts_code"]
                if api == "daily" and symbol not in reject:
                    db.bars[symbol] = {"symbol": symbol, **row, "limit_up": None, "limit_down": None,
                                       "adj_factor": None, "is_suspended": False, "quality_status": "fresh"}
                elif api == "stk_limit":
                    db.bars[symbol]["limit_up"], db.bars[symbol]["limit_down"] = row["up_limit"], row["down_limit"]
                elif api == "adj_factor":
                    db.bars[symbol]["adj_factor"] = row["adj_factor"]
            return len(rows)

        async def db_runner(action, *args, **kwargs):
            if persistence_error and getattr(action, "__name__", "") == "persist":
                raise persistence_error
            return action(*args)

        async def public_runner(action, *args, **kwargs):
            calls["vendor"].append({"symbols": list(args[1]), "timeout": kwargs["timeout_seconds"]})
            if during_vendor:
                during_vendor(db)
            if vendor_error:
                raise vendor_error
            return list(quotes), health

        patches = [
            patch.object(completion, "_read_state", return_value=state),
            patch.object(completion, "_day_bars", side_effect=day_bars),
            patch.object(completion, "_day_ratios", return_value=ratios or {}),
            patch.object(completion, "_factor_state", return_value=list(factors)),
            patch.object(completion, "merge_cross_section",
                         return_value=type("Merged", (), {"daily_rows": list(quotes)})()),
        ]
        if agreement is not None:
            patches.append(patch.object(completion, "limit_rule_agreement", return_value=agreement))
        for active in patches:
            active.start()
        try:
            factory = (lambda: (_ for _ in ()).throw(source_error)) if source_error else Source
            result = asyncio.run(completion.complete(
                DAY, db=db, run_public_blocking=public_runner, run_database_blocking=db_runner,
                persist_rows=persist_rows, read_source_factory=factory, now=NOW,
            ))
        finally:
            for active in reversed(patches):
                active.stop()
        return result, db, calls

    def test_filled_bar_limits_and_factor_are_written(self):
        reference = bar("000099.SZ")
        state = {"roster": ["000001.SZ", "000099.SZ"], "bars": [reference]}
        factors = [{"symbol": "000001.SZ", "prev_date": DAY, "prev_factor": 1, "next_factor": None}]
        result, db, _calls = self.run_completion(
            state=state, quotes=[quote("000001.SZ")], outcomes={"filled": ["000001.SZ"]},
            ratios={"000001.SZ": [0.10], "000099.SZ": [0.10]}, factors=factors,
        )
        self.assertEqual(result["status"], "completed")
        self.assertEqual(db.bars["000001.SZ"]["limit_up"], "11.00")
        self.assertEqual(db.bars["000001.SZ"]["adj_factor"], "1")

    def test_each_specific_limit_pending_reason_is_reported(self):
        cases = ((None, [0.10], "no_previous_close"), (10, [], "no_earlier_bar_with_limits"),
                 (10, [0.15], "unusual_limit_ratio"))
        for pre_close, ratios, reason in cases:
            with self.subTest(reason=reason):
                pending = bar("000001.SZ", pre_close=pre_close, limit_up=None, limit_down=None)
                reference = bar("000099.SZ")
                state = {"roster": ["000001.SZ", "000099.SZ"], "bars": [pending, reference]}
                result, _db, _calls = self.run_completion(
                    state=state, ratios={"000001.SZ": ratios, "000099.SZ": [0.10]},
                )
                self.assertEqual(result["limits_pending"], {"000001.SZ": reason})

    def test_agreement_below_995_refuses_limits(self):
        pending = bar("000001.SZ", limit_up=None, limit_down=None)
        state = {"roster": ["000001.SZ"], "bars": [pending]}
        result, db, _calls = self.run_completion(state=state, ratios={"000001.SZ": [0.10]}, agreement=0.994)
        self.assertEqual(result["limits_pending"], {"000001.SZ": "agreement_below_0.995"})
        self.assertIsNone(db.bars["000001.SZ"]["limit_up"])

    def test_bar_arriving_during_vendor_call_is_not_written(self):
        state = {"roster": ["000001.SZ"], "bars": []}
        def arrive(db):
            db.bars["000001.SZ"] = bar("000001.SZ")
        _result, _db, calls = self.run_completion(
            state=state, quotes=[quote("000001.SZ")], outcomes={"filled": ["000001.SZ"]},
            ratios={"000001.SZ": [0.10]}, during_vendor=arrive,
        )
        daily = next(rows for api, rows in calls["persist"] if api == "daily")
        self.assertEqual(daily, [])

    def test_rejected_bar_gets_no_controls(self):
        state = {"roster": ["000001.SZ"], "bars": []}
        result, _db, calls = self.run_completion(
            state=state, quotes=[quote("000001.SZ")], outcomes={"filled": ["000001.SZ"]},
            ratios={"000001.SZ": [0.10]}, reject={"000001.SZ"},
        )
        self.assertEqual(result["bar_rejected"], ["000001.SZ"])
        self.assertFalse(any(rows for api, rows in calls["persist"] if api in ("stk_limit", "adj_factor")))

    def test_unusable_existing_makes_run_partial(self):
        unusable = bar("000001.SZ", open_price=None, quality_status="stale")
        result, _db, _calls = self.run_completion(state={"roster": ["000001.SZ"], "bars": [unusable]})
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["unusable_existing"], ["000001.SZ"])

    def test_blocked_transport_writes_receipt(self):
        result, db, _calls = self.run_completion(
            state={"roster": [], "bars": []}, source_error=RuntimeError("transport absent"),
        )
        self.assertEqual(result["status"], "blocked")
        self.assertTrue(any("fetch_runs" in sql for sql, _params in db.statements))

    def test_nothing_to_do_writes_receipt_without_vendor_call(self):
        complete_bar = bar("000001.SZ")
        result, db, calls = self.run_completion(state={"roster": ["000001.SZ"], "bars": [complete_bar]})
        self.assertEqual(result["status"], "completed")
        self.assertEqual(calls["vendor"], [])
        self.assertTrue(any("fetch_runs" in sql for sql, _params in db.statements))

    def test_second_run_completes_only_pending_limits(self):
        pending = bar("000001.SZ", limit_up=None, limit_down=None)
        complete_bar = bar("000002.SZ")
        result, db, calls = self.run_completion(
            state={"roster": ["000001.SZ", "000002.SZ"], "bars": [pending, complete_bar]},
            ratios={"000001.SZ": [0.10], "000002.SZ": [0.10]},
        )
        self.assertEqual(result["status"], "completed")
        self.assertEqual(calls["vendor"], [])
        self.assertEqual(db.bars["000001.SZ"]["limit_up"], "11.00")

    def test_failed_request_error_is_in_receipt(self):
        result, db, _calls = self.run_completion(
            state={"roster": ["000001.SZ"], "bars": []},
            outcomes={"failed": ["000001.SZ"]}, errors={"000001.SZ": "TimeoutError: timed out"},
        )
        self.assertEqual(result["errors"], {"000001.SZ": "TimeoutError: timed out"})
        self.assertEqual(db.statements[-1][1][-1].obj["errors"], {"000001.SZ": "TimeoutError: timed out"})

    def test_work_set_over_600_is_blocked_without_vendor_call(self):
        roster = [f"{index:06d}.SZ" for index in range(601)]
        result, db, calls = self.run_completion(state={"roster": roster, "bars": []})
        self.assertEqual(result["reason"], "close_or_controls_not_landed")
        self.assertEqual(result["missing_bar_count"], 601)
        self.assertEqual(calls["vendor"], [])
        self.assertTrue(any("fetch_runs" in sql for sql, _params in db.statements))

    def test_vendor_runner_failure_writes_failed_receipt(self):
        result, db, _calls = self.run_completion(
            state={"roster": ["000001.SZ"], "bars": []}, vendor_error=TimeoutError("vendor timeout"),
        )
        self.assertEqual((result["status"], result["reason"]), ("failed", "TimeoutError: vendor timeout"))
        self.assertTrue(any("fetch_runs" in sql for sql, _params in db.statements))

    def test_persistence_runner_failure_writes_failed_receipt(self):
        result, db, _calls = self.run_completion(
            state={"roster": [], "bars": []}, persistence_error=TimeoutError("database timeout"),
        )
        self.assertEqual((result["status"], result["reason"]), ("failed", "TimeoutError: database timeout"))
        self.assertTrue(any("fetch_runs" in sql for sql, _params in db.statements))

    def test_nonpositive_factor_is_in_vendor_work_set(self):
        bad = bar("000001.SZ", adj_factor=0)
        _result, _db, calls = self.run_completion(
            state={"roster": ["000001.SZ"], "bars": [bad]}, outcomes={"not_traded": ["000001.SZ"]},
        )
        self.assertEqual(calls["vendor"][0]["symbols"], ["000001.SZ"])


if __name__ == "__main__":
    unittest.main()
