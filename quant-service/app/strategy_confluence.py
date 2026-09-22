"""Same-session confluence between independently applied intraday strategies.

The watchlist scan, 小杰龙头 (incl. 潜龙出海) and teacher-review plans each run
their own rules in the same scan cycle and keep their own alert budgets.  This
book only answers "was this symbol also selected by the other strategy today?"
so each alert can say so.  It never creates, upgrades or suppresses a signal.

It is process memory rebuilt from persisted evidence (``hydrate_*``) after a
restart; post-close settlement recomputes confluence from the database.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Iterable, Mapping


class ConfluenceBook:
    def __init__(self) -> None:
        self._day: date | None = None
        self._xiaojie: dict[str, set[str]] = {}
        self._teacher: dict[str, dict[str, Any]] = {}
        self.xiaojie_hydrated = False
        self.teacher_refreshed_at: Any = None

    def _roll(self, day: date) -> None:
        if self._day != day:
            self._day, self._xiaojie, self._teacher = day, {}, {}
            self.xiaojie_hydrated, self.teacher_refreshed_at = False, None

    def note_xiaojie(self, day: date, candidates: Iterable[Mapping[str, Any]]) -> None:
        self._roll(day)
        for candidate in candidates:
            symbol = str(candidate.get("symbol") or "").upper()
            if symbol:
                self._xiaojie.setdefault(symbol, set()).add(str(candidate.get("mode") or "unclassified"))

    def hydrate_xiaojie(self, day: date, rows: Iterable[Mapping[str, Any]]) -> None:
        # Shadow rows (a gated 潜龙 name is stored as no_trade) are evidence
        # for scoring the gate, not a detection another strategy may cite.
        self.note_xiaojie(day, [row for row in rows if row.get("decision") != "no_trade"])
        self.xiaojie_hydrated = True

    def set_teacher_plans(self, day: date, plans: Mapping[str, Mapping[str, Any]], refreshed_at: Any) -> None:
        self._roll(day)
        self._teacher = {str(symbol).upper(): dict(plan) for symbol, plan in plans.items()}
        self.teacher_refreshed_at = refreshed_at

    def xiaojie_modes(self, day: date, symbol: str) -> list[str]:
        return sorted(self._xiaojie.get(str(symbol).upper(), ())) if self._day == day else []

    def teacher_plan(self, day: date, symbol: str) -> dict[str, Any] | None:
        return self._teacher.get(str(symbol).upper()) if self._day == day else None


def teacher_plans_for_day(rows: Iterable[Mapping[str, Any]], day: date) -> dict[str, dict[str, Any]]:
    """Compact active teacher plans for ``day`` from watchlist rows."""
    plans: dict[str, dict[str, Any]] = {}
    for row in rows:
        plan = (row.get("metadata") or {}).get("teacher_review") if isinstance(row.get("metadata"), Mapping) else None
        if not isinstance(plan, Mapping) or plan.get("status", "active") != "active":
            continue
        if str(plan.get("session_date") or "") != day.isoformat():
            continue
        plans[str(row["symbol"]).upper()] = {
            key: plan.get(key) for key in ("analyst_id", "review_date", "playbook", "stance", "group", "pack_id")
        }
    return plans


def xiaojie_confluence_line(modes: list[str]) -> str | None:
    return f"多策略共振：小杰龙头 {'/'.join(modes)} 今日同样选中" if modes else None


def teacher_confluence_line(plan: Mapping[str, Any] | None) -> str | None:
    if not plan:
        return None
    stance = {"positive": "看好", "watch": "观察", "negative": "否定", "unknown": "点名"}.get(str(plan.get("stance")), "点名")
    return (f"多策略共振：老师复盘 {plan.get('analyst_id')} {plan.get('review_date')} {stance}"
            f"（{plan.get('group') or ''}，{plan.get('playbook')}）")


__all__ = ["ConfluenceBook", "teacher_confluence_line", "teacher_plans_for_day", "xiaojie_confluence_line"]
