"""Pure, read-only evidence gates for Longhu post-close research replay.

These gates establish that a comparable set of provider contracts was captured
on a sequence of exchange sessions.  They deliberately do not decode vendor
fields, estimate alpha, or promote any feature into a strategy.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Iterable, Mapping


REQUIRED_CONTRACTS = frozenset({
    ("longhu_market_wide", "RiseFallAnalysis"),
    ("longhu_market_wide", "MoodNumCount"),
    ("longhu_market_wide", "GetPlateInfo_w38"),
    ("longhu_quote", "DailyLimitPerformance2"),
    ("longhu_history", "MorningBiddingList"),
    ("longhu_history", "DailyLimitPerformance"),
    ("longhu_history", "DailyLimitPerformance2"),
    ("longhu_article", "GetTopList"),
    ("longhu_article", "GetList"),
    ("longhu_lhb", "InfoList"),
})
REPLAY_WINDOWS = (30, 60, 90)
WALK_FORWARD_TRAIN_DAYS = 60
WALK_FORWARD_TEST_DAYS = 20
WALK_FORWARD_EMBARGO_DAYS = 5


def _as_date(value: Any) -> date | None:
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def _session_contracts(rows: Iterable[Mapping[str, Any]]) -> dict[date, set[tuple[str, str]]]:
    sessions: dict[date, set[tuple[str, str]]] = {}
    for row in rows:
        session = _as_date(row.get("exchange_date"))
        target, action = str(row.get("target") or ""), str(row.get("action") or "")
        if session is None or not target or not action:
            continue
        sessions.setdefault(session, set()).add((target, action))
    return sessions


def _window_payload(sessions: list[date], contracts_by_session: Mapping[date, set[tuple[str, str]]], window: int) -> dict[str, Any]:
    sampled = sessions[-window:]
    complete = [item for item in sampled if REQUIRED_CONTRACTS.issubset(contracts_by_session[item])]
    return {
        "window_sessions": window,
        "observed_sessions": len(sampled),
        "complete_sessions": len(complete),
        "status": "ready" if len(complete) >= window else "accumulating",
        "first_session": str(sampled[0]) if sampled else None,
        "latest_session": str(sampled[-1]) if sampled else None,
    }


def _walk_forward_folds(complete_sessions: list[date]) -> list[dict[str, Any]]:
    minimum = WALK_FORWARD_TRAIN_DAYS + WALK_FORWARD_EMBARGO_DAYS + WALK_FORWARD_TEST_DAYS
    folds: list[dict[str, Any]] = []
    for start in range(0, len(complete_sessions) - minimum + 1, WALK_FORWARD_TEST_DAYS):
        train = complete_sessions[start:start + WALK_FORWARD_TRAIN_DAYS]
        test_start = start + WALK_FORWARD_TRAIN_DAYS + WALK_FORWARD_EMBARGO_DAYS
        test = complete_sessions[test_start:test_start + WALK_FORWARD_TEST_DAYS]
        folds.append({
            "train_start": str(train[0]), "train_end": str(train[-1]),
            "embargo_sessions": WALK_FORWARD_EMBARGO_DAYS,
            "test_start": str(test[0]), "test_end": str(test[-1]),
        })
    return folds


def assess(rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Return conservative 30/60/90-session evidence readiness only."""
    contracts_by_session = _session_contracts(rows)
    sessions = sorted(contracts_by_session)
    complete_sessions = [item for item in sessions if REQUIRED_CONTRACTS.issubset(contracts_by_session[item])]
    windows = [_window_payload(sessions, contracts_by_session, window) for window in REPLAY_WINDOWS]
    folds = _walk_forward_folds(complete_sessions)
    ready = len(complete_sessions) >= 90 and bool(folds)
    return {
        "status": "ready" if ready else "accumulating",
        "research_only": True,
        "replay_only": True,
        "live_effect": "none",
        "required_contracts": [f"{target}:{action}" for target, action in sorted(REQUIRED_CONTRACTS)],
        "sessions_observed": len(sessions),
        "sessions_complete": len(complete_sessions),
        "windows": windows,
        "walk_forward": {
            "status": "ready" if folds else "blocked",
            "train_sessions": WALK_FORWARD_TRAIN_DAYS,
            "test_sessions": WALK_FORWARD_TEST_DAYS,
            "embargo_sessions": WALK_FORWARD_EMBARGO_DAYS,
            "folds": folds,
        },
        "policy": "Contract coverage gate only: field schema, units, outcomes, costs and promotion remain separate gates.",
    }


__all__ = [
    "REPLAY_WINDOWS", "REQUIRED_CONTRACTS", "WALK_FORWARD_EMBARGO_DAYS",
    "WALK_FORWARD_TEST_DAYS", "WALK_FORWARD_TRAIN_DAYS", "assess",
]
