from datetime import date
from decimal import Decimal

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
