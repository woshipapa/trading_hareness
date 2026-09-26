"""What happens to a teacher's plan after its first session.

Operator policy (2026-09-22): a stock the teacher called that has *already
satisfied* its plan - a relay name that sealed another board, a trend name
that closed through its trigger or held a confirmed entry - is promoted and
lives on into the next session; one that has not satisfied it yet is only
observed; the newest review always overrides an older one for the same stock.

States, decided once per close from that session's evidence:

* ``new``      - planned from the newest review for its first session (alerts);
* ``promoted`` - satisfied; carried as a hold plan (守 MA5, 过当日高点确认,
  收盘破 MA10 失效) for at most ``PROMOTED_MAX_CARRY`` sessions (alerts);
* ``observe``  - not satisfied yet; stays in the watch pool so the scan tape,
  the daily review and tomorrow's close check still see it, but the plan is
  inert intraday - no teacher entry alert.  A close that satisfies the plan
  promotes it; the observation ends ``OBSERVE_GRACE_SESSIONS`` after the
  teacher's own window;
* ``expired``  - invalidated, observation over, or the promoted carry used up.

Pure functions only; the roll in ``teacher_review_service`` does the I/O.
"""

from __future__ import annotations

from typing import Any, Mapping

from .teacher_review_playbooks import playbook_kind

PROMOTED_MAX_CARRY = 5
OBSERVE_GRACE_SESSIONS = 2
#: Plan status per state; ``teacher_review_rules.active_plan`` evaluates only "active".
STATUS = {"new": "active", "promoted": "active", "observe": "observe"}
STATE_LABELS = {"new": "新计划", "promoted": "晋级延续", "observe": "观察", "expired": "退出"}


def close_trigger(plan: Mapping[str, Any]) -> float | None:
    """The level a close must clear for a trend plan to count as satisfied."""
    playbook = str(plan.get("playbook") or "")
    params = plan.get("params") or {}
    extra = plan.get("extra") or {}
    value = None
    if playbook in {"prior_high_breakout", "trend_continuation", "ma5_reclaim_or_divergence"}:
        value = params.get("prior_high")
    elif playbook == "platform_breakout":
        value = extra.get("platform_upper")
    elif playbook == "double_bottom_platform":
        value = params.get("platform_high")
    elif playbook == "ma60_reclaim":
        value = extra.get("ma60")
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def ma_at_close(plan: Mapping[str, Any], period: int, close: float) -> float | None:
    """The session's own MA_n at ``close``, from the prefix frozen with the plan."""
    prefix = ((plan.get("extra") or {}).get("ma_prefix") or {}).get(str(period))
    return None if prefix is None else (float(prefix) + close) / period


def decide(plan: Mapping[str, Any], fact: Mapping[str, Any] | None) -> dict[str, Any]:
    """Next state for a plan that was live on the settled session.

    ``fact`` is the settlement row for the stock (``bar``, ``closed_at_limit``,
    ``entry``, ``invalidated_at``).  Returns ``{"state", "reason", "carried"}``.
    """
    lifecycle = plan.get("lifecycle") or {}
    state = str(lifecycle.get("state") or "new")
    carried = int(lifecycle.get("carried") or 0)
    bar = (fact or {}).get("bar") or {}
    close = bar.get("close")
    if close is None:
        # No evidence for the session: nothing is promoted or ended on a gap.
        keep = "promoted" if state == "promoted" else "observe"
        return {"state": keep, "reason": "当日行情缺失，保持原状态", "carried": carried}
    close = float(close)
    invalidated = bool((fact or {}).get("invalidated_at"))

    if state == "promoted":
        ma5, ma10 = ma_at_close(plan, 5, close), ma_at_close(plan, 10, close)
        if invalidated or (ma10 is not None and close < ma10):
            return {"state": "expired", "reason": f"收盘跌破MA10 {ma10:.2f}" if ma10 else "盘中失效", "carried": carried}
        if carried + 1 >= PROMOTED_MAX_CARRY:
            return {"state": "expired", "reason": f"晋级延续已满{PROMOTED_MAX_CARRY}个交易日", "carried": carried + 1}
        if ma5 is not None and close < ma5:
            return {"state": "observe", "reason": f"收盘跌破MA5 {ma5:.2f}，转观察", "carried": carried + 1}
        return {"state": "promoted", "reason": "守住MA5，继续晋级延续", "carried": carried + 1}

    kind = playbook_kind(str(plan.get("playbook") or ""))
    if kind == "relay" and (fact or {}).get("closed_at_limit"):
        return {"state": "promoted", "reason": "封板晋级", "carried": 0}
    trigger = close_trigger(plan)
    if trigger is not None and close > trigger:
        return {"state": "promoted", "reason": f"收盘站上 {trigger:g}", "carried": 0}
    entry = (fact or {}).get("entry") or {}
    entry_price = entry.get("price")
    if entry and not invalidated and entry_price is not None and close >= float(entry_price):
        return {"state": "promoted", "reason": f"盘中触发且收盘守住 {float(entry_price):g}", "carried": 0}
    if invalidated:
        return {"state": "expired", "reason": "盘中失效", "carried": 0}
    # A carried plan's own valid_sessions is widened to stay plannable; the
    # teacher's window is kept in the lifecycle record.
    index = int(plan.get("session_index") or 1)
    valid = int(lifecycle.get("teacher_valid_sessions") or plan.get("valid_sessions") or 1)
    if index >= valid + OBSERVE_GRACE_SESSIONS:
        return {"state": "expired", "reason": f"观察期满（老师窗口 {valid} 日 + {OBSERVE_GRACE_SESSIONS} 日）", "carried": 0}
    return {"state": "observe", "reason": "尚未满足，转观察", "carried": 0}


def continuation_stock(stock: Mapping[str, Any], bar: Mapping[str, Any], reason: str) -> dict[str, Any]:
    """The hold plan a promoted stock carries into the next session."""
    return {
        **{key: stock[key] for key in ("code", "name", "group", "teacher", "evidence") if key in stock},
        "stance": "positive", "playbook": "trend_continuation",
        "params": {
            "prior_high": round(float(bar["high"]), 3),
            "prior_high_src": f"D（晋级日最高价；{reason}）",
            "hold_ma": 5, "hold_ma_src": "I（晋级后沿5日线持有）",
            "floor_ma": 10, "floor_ma_src": "I（收盘跌破10日线退出）",
        },
        "invalidation": "收盘跌破当日MA10",
        "original_playbook": stock.get("playbook"),
    }


__all__ = [
    "OBSERVE_GRACE_SESSIONS", "PROMOTED_MAX_CARRY", "STATE_LABELS", "STATUS",
    "close_trigger", "continuation_stock", "decide", "ma_at_close",
]
