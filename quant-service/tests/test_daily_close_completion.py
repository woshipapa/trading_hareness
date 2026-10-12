from datetime import date
from decimal import Decimal
import asyncio
from contextlib import contextmanager
from unittest.mock import patch

import app.daily_close_completion as completion
from app.daily_close_completion import completion_status, factor_rows, limit_rows, limit_rule_agreement


def test_carried_limits_round_beijing_toward_previous_close():
    rows = limit_rows([{"symbol": "920001.BJ", "pre_close": "10.005", "ratios": ["0.30", "0.30"]}], date(2026, 10, 12))
    assert rows[0]["up_limit"] == "13.00"
    assert rows[0]["down_limit"] == "7.01"


def test_limit_agreement_ignores_bars_without_stored_limits():
    bars = [{"symbol": "000001.SZ", "pre_close": 10, "ratios": [0.1], "limit_up": 11, "limit_down": 9},
            {"symbol": "000002.SZ", "pre_close": 10, "ratios": [0.1], "limit_up": None, "limit_down": None}]
    assert limit_rule_agreement(bars) == 1.0


def test_factor_carry_requires_stable_neighbours_and_ex_rights_free_day():
    result = factor_rows([
        {"symbol": "000001.SZ", "prev_date": date(2026, 10, 10), "prev_factor": Decimal("1.2"),
         "next_date": date(2026, 10, 13), "next_factor": Decimal("1.2")},
        {"symbol": "000002.SZ", "prev_factor": 1, "next_factor": 2},
        {"symbol": "000003.SZ", "prev_factor": 1, "ex_rights_on_day": True},
    ], date(2026, 10, 12))
    assert len(result["rows"]) == 1
    assert result["pending"] == {"000002.SZ": "factor_changes_across_session", "000003.SZ": "ex_rights_on_session"}


def test_status_is_partial_for_any_unresolved_reason():
    assert completion_status({"filled": [], "not_traded": ["A"], "adjusted_later": [], "unverified": [], "invalid": [], "failed": []}) == "completed"
    assert completion_status({"filled": [], "not_traded": [], "adjusted_later": ["A"]}) == "partial"


class _CompletionDb:
    def __init__(self, bars):
        self.bars = bars
        self.statements = []

    @contextmanager
    def transaction(self):
        yield self

    def execute(self, statement, params=None):
        self.statements.append((statement, params))


def _quote(symbol, pre_close=10):
    return {"ts_code": symbol, "trade_date": "20261012", "open": 10, "high": 11,
            "low": 9, "close": 10.5, "pre_close": pre_close, "vol": 100, "amount": 1000}


def test_complete_reads_and_writes_controls_in_one_transaction():
    day = date(2026, 10, 12)
    db = _CompletionDb({
        "000002.SZ": {"symbol": "000002.SZ", "open": 10, "high": 11, "low": 9, "close": 10,
                       "pre_close": 10, "limit_up": 11, "limit_down": 9, "adj_factor": 1,
                       "is_suspended": False, "quality_status": "fresh"},
    })
    state = {"roster": ["000001.SZ", "000002.SZ"], "bars": list(db.bars.values()),
             "ratios": {"000001.SZ": [0.10], "000002.SZ": [0.10]},
             "factors": [{"symbol": "000001.SZ", "prev_date": date(2026, 10, 11), "prev_factor": 1,
                          "next_date": None, "next_factor": None}]}

    def day_bars(_connection, _day, symbols=None):
        return [dict(row) for symbol, row in db.bars.items() if symbols is None or symbol in symbols]

    def persist_rows(_connection, api, _key, rows, _provider, _available):
        for row in rows:
            symbol = row["ts_code"]
            if api == "daily":
                db.bars[symbol] = {"symbol": symbol, **row, "limit_up": None, "limit_down": None,
                                   "adj_factor": None, "is_suspended": False, "quality_status": "fresh"}
            elif api == "stk_limit":
                db.bars[symbol]["limit_up"], db.bars[symbol]["limit_down"] = row["up_limit"], row["down_limit"]
            else:
                db.bars[symbol]["adj_factor"] = row["adj_factor"]
        return len(rows)

    async def db_runner(action, *args, **kwargs):
        return action(*args)

    async def public_runner(action, *args, **kwargs):
        return action(*args)

    with patch.object(completion, "_read_state", return_value=state), \
         patch.object(completion, "_day_bars", side_effect=day_bars), \
         patch.object(completion, "_day_ratios", return_value=state["ratios"]), \
         patch.object(completion, "_factor_state", return_value=state["factors"]), \
         patch.object(completion, "fetch_classified", return_value=([_quote("000001.SZ")], {
             "outcomes": {"filled": ["000001.SZ"], "not_traded": [], "adjusted_later": [], "unverified": [], "invalid": [], "failed": []},
             "errors": {}, "ex_rights_on_day": {"000001.SZ": False}})), \
         patch.object(completion, "merge_cross_section", return_value=type("Merged", (), {"daily_rows": [_quote("000001.SZ")]})()):
        result = asyncio.run(completion.complete(day, db=db, run_public_blocking=public_runner,
                                                 run_database_blocking=db_runner, persist_rows=persist_rows,
                                                 read_source_factory=lambda: object()))
    assert result["status"] == "completed"
    assert db.bars["000001.SZ"]["limit_up"] == "11.00"
    assert db.bars["000001.SZ"]["adj_factor"] == "1"
    assert any("fetch_runs" in statement for statement, _params in db.statements)


def test_complete_marks_rejected_bar_and_failed_request_partial():
    day = date(2026, 10, 12)
    db = _CompletionDb({})
    state = {"roster": ["000001.SZ"], "bars": [], "ratios": {"000001.SZ": [0.10]}, "factors": []}

    async def db_runner(action, *args, **kwargs):
        return action(*args)

    async def public_runner(action, *args, **kwargs):
        return ([ _quote("000001.SZ") ], {"outcomes": {"filled": ["000001.SZ"], "not_traded": [], "adjusted_later": [], "unverified": [], "invalid": [], "failed": ["000002.SZ"]}, "errors": {"000002.SZ": "TimeoutError: timed out"}, "ex_rights_on_day": {"000001.SZ": False}})

    with patch.object(completion, "_read_state", return_value=state), \
         patch.object(completion, "_day_bars", return_value=[]), \
         patch.object(completion, "_day_ratios", return_value=state["ratios"]), \
         patch.object(completion, "_factor_state", return_value=[]), \
         patch.object(completion, "merge_cross_section", return_value=type("Merged", (), {"daily_rows": [_quote("000001.SZ")]})()), \
         patch.object(completion, "fetch_classified"):
        result = asyncio.run(completion.complete(day, db=db, run_public_blocking=public_runner,
                                                 run_database_blocking=db_runner, persist_rows=lambda *args: 0,
                                                 read_source_factory=lambda: object()))
    assert result["status"] == "partial"
    assert result["bar_rejected"] == ["000001.SZ"]
