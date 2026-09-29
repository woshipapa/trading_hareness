"""Every deliberate change to a strategy, with what we expected before we knew.

The outcome review counts what our conditions missed; acting on that count is
where research usually goes wrong.  A threshold gets nudged, the next week
looks better, and nobody can say whether the nudge helped or the tape did.

So a change is recorded before it ships, with:

* what changed, from what to what, and which review motivated it;
* a **preregistered expectation** - the measure that should move, the
  direction, and how many sessions to wait.  Writing it down first is the
  whole point: an expectation invented after the result is not evidence;
* the commit that carried it, so the archive and the code agree.

``evaluate`` then reads the outcome reviews on either side of the change and
reports the measure before and after.  It never decides anything - a change
that missed its expectation stays in the log, visible, until a person
reverts or supersedes it.

Stored as observations (capability ``strategy_change``), so no owner DDL.
Research only.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from datetime import date, datetime, timezone
from typing import Any

from psycopg.types.json import Json

PROVIDER = "strategy_ops"
CAPABILITY = "strategy_change"
#: A change is not judged before this many sessions unless it says otherwise.
DEFAULT_REVIEW_SESSIONS = 20
STATUSES = ("proposed", "applied", "reverted", "superseded")
#: The measures a preregistered expectation may name, as the outcome review
#: reports them per playbook.
MEASURES = ("hit_rate_pct", "missed_rate_pct", "net_mean_pct", "excess_mean_pct", "net_next_day_mean_pct")
DIRECTIONS = ("up", "down")


def change_id(change: Mapping[str, Any]) -> str:
    body = {key: value for key, value in change.items() if key not in {"change_id", "recorded_at"}}
    return hashlib.sha256(json.dumps(body, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()[:16]


def validate(change: Mapping[str, Any]) -> list[str]:
    """Refuse a change that cannot be judged later."""
    problems: list[str] = []
    for field in ("strategy", "scope", "parameter", "reason", "applied_on"):
        if not str(change.get(field) or "").strip():
            problems.append(f"{field} is required")
    if "from" not in change or "to" not in change:
        problems.append("from and to are required, even when one of them is null")
    if str(change.get("status") or "applied") not in STATUSES:
        problems.append(f"status must be one of {', '.join(STATUSES)}")
    expectation = change.get("expectation")
    if not isinstance(expectation, Mapping):
        problems.append("expectation is required: name the measure, the direction and the window")
        return problems
    if str(expectation.get("measure")) not in MEASURES:
        problems.append(f"expectation.measure must be one of {', '.join(MEASURES)}")
    if str(expectation.get("direction")) not in DIRECTIONS:
        problems.append("expectation.direction must be up or down")
    sessions = expectation.get("review_after_sessions", DEFAULT_REVIEW_SESSIONS)
    if not isinstance(sessions, int) or not 1 <= sessions <= 120:
        problems.append("expectation.review_after_sessions must be an integer between 1 and 120")
    try:
        date.fromisoformat(str(change.get("applied_on")))
    except ValueError:
        problems.append("applied_on must be an ISO date")
    return problems


def record(database: Any, change: dict[str, Any], *, recorded_at: datetime | None = None) -> dict[str, Any]:
    """Archive one change.  Re-recording the same content is a no-op."""
    problems = validate(change)
    if problems:
        return {"status": "rejected", "problems": problems}
    at = recorded_at or datetime.now(timezone.utc)
    body = {**change, "change_id": change_id(change), "recorded_at": at.isoformat(),
            "provider_key": PROVIDER, "capability": CAPABILITY, "research_only": True, "live_effect": "none"}
    serialized = json.dumps(body, ensure_ascii=False, sort_keys=True, default=str)
    effective = datetime.combine(date.fromisoformat(str(change["applied_on"])), datetime.min.time(),
                                 tzinfo=timezone.utc)
    with database.transaction() as connection:
        connection.execute(
            """INSERT INTO quant.raw_market_observations(provider_key,capability,market,symbol,effective_at,
                   available_at,payload_sha256,normalized,payload)
               VALUES(%s,%s,'cn',%s,%s,%s,%s,%s,%s)
               ON CONFLICT(provider_key,capability,market,symbol,effective_at,payload_sha256) DO NOTHING""",
            (PROVIDER, CAPABILITY, f"strategy:{change['strategy']}", effective, at,
             hashlib.sha256(serialized.encode()).hexdigest(), Json(body), Json(body)),
        )
    return {"status": "recorded", "change_id": body["change_id"], "research_only": True, "live_effect": "none"}


def recent(database: Any, *, limit: int = 50) -> list[dict[str, Any]]:
    with database.transaction() as connection:
        rows = connection.execute(
            """SELECT payload FROM quant.raw_market_observations
                WHERE provider_key=%s AND capability=%s
                ORDER BY effective_at DESC, available_at DESC LIMIT %s""",
            (PROVIDER, CAPABILITY, max(1, min(int(limit), 200))),
        ).fetchall()
    return [row["payload"] for row in rows]


def _measure_for(report: Mapping[str, Any], scope: str, measure: str) -> float | None:
    """One measure for one playbook (or the whole session when scope is ``all``)."""
    rows = ((report.get("learning") or {}).get("playbooks") or [])
    if scope == "all":
        values = [row.get(measure) for row in rows if row.get(measure) is not None]
        return round(sum(values) / len(values), 4) if values else None
    row = next((item for item in rows if str(item.get("playbook")) == scope), None)
    value = (row or {}).get(measure)
    return None if value is None else float(value)


def _difference_significance(before: Sequence[float], after: Sequence[float]) -> str:
    """Use a conservative Welch-style normal approximation for change review.

    This is a descriptive guard, not a promotion test.  With fewer than two
    observations on either side there is no variance estimate, so the caller
    keeps the historical directional verdict for compatibility and reports the
    result as ``insufficient_variance``.
    """
    if len(before) < 2 or len(after) < 2:
        return "insufficient_variance"
    before_mean = sum(before) / len(before)
    after_mean = sum(after) / len(after)
    before_var = sum((value - before_mean) ** 2 for value in before) / (len(before) - 1)
    after_var = sum((value - after_mean) ** 2 for value in after) / (len(after) - 1)
    standard_error = math.sqrt(before_var / len(before) + after_var / len(after))
    if standard_error <= 0:
        return "significant" if before_mean != after_mean else "inconclusive"
    z = abs(after_mean - before_mean) / standard_error
    return "significant" if z >= 1.96 else "inconclusive"


def evaluate(changes: Sequence[Mapping[str, Any]], reports: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Each applied change against its own preregistered expectation.

    ``reports`` are archived outcome reviews.  Sessions are split strictly
    before and on/after ``applied_on``; a session the change did not yet
    affect can never be counted as evidence for it.
    """
    by_date = {str(report.get("trade_date")): report for report in reports if report.get("trade_date")}
    out = []
    for change in changes:
        if str(change.get("status") or "applied") != "applied":
            continue
        expectation = change.get("expectation") or {}
        measure, scope = str(expectation.get("measure")), str(change.get("scope"))
        applied_on = str(change.get("applied_on"))
        before, after = [], []
        for trade_date, report in by_date.items():
            value = _measure_for(report, scope, measure)
            if value is None:
                continue
            (after if trade_date >= applied_on else before).append(value)
        window = int(expectation.get("review_after_sessions", DEFAULT_REVIEW_SESSIONS))
        before_mean = round(sum(before) / len(before), 4) if before else None
        after_mean = round(sum(after) / len(after), 4) if after else None
        moved = (None if before_mean is None or after_mean is None
                 else round(after_mean - before_mean, 4))
        significance = _difference_significance(before, after) if moved is not None else "insufficient_variance"
        if moved is None or len(after) < window:
            verdict = "too_early"
        elif significance == "inconclusive":
            verdict = "inconclusive"
        elif (moved > 0) == (str(expectation.get("direction")) == "up"):
            verdict = "as_expected"
        else:
            verdict = "against_expectation"
        out.append({
            "change_id": change.get("change_id"), "strategy": change.get("strategy"), "scope": scope,
            "parameter": change.get("parameter"), "from": change.get("from"), "to": change.get("to"),
            "applied_on": applied_on, "measure": measure, "direction": expectation.get("direction"),
            "sessions_before": len(before), "sessions_after": len(after), "review_after_sessions": window,
            "before": before_mean, "after": after_mean, "moved": moved, "verdict": verdict,
            "significance": significance,
            "reason": change.get("reason"),
        })
    return out


def change_lines(evaluations: Sequence[Mapping[str, Any]]) -> list[str]:
    """Report lines: what we changed, what we expected, what happened."""
    labels = {"too_early": "样本不足", "inconclusive": "差异不确定", "as_expected": "符合预期", "against_expectation": "与预期相反"}
    lines = []
    for item in evaluations:
        moved = "—" if item["moved"] is None else f"{item['moved']:+.2f}"
        lines.append(
            f"{item['strategy']}·{item['scope']} {item['parameter']} {item['from']}→{item['to']}"
            f"（{item['applied_on']} 起）｜预期 {item['measure']} {item['direction']}｜"
            f"改前 {item['before']} → 改后 {item['after']}（{moved}）｜"
            f"{labels.get(item['verdict'], item['verdict'])}"
            f"（{item['sessions_after']}/{item['review_after_sessions']} 个交易日）")
    return lines


__all__ = [
    "CAPABILITY", "DEFAULT_REVIEW_SESSIONS", "DIRECTIONS", "MEASURES", "PROVIDER", "STATUSES",
    "change_id", "change_lines", "evaluate", "recent", "record", "validate",
]
