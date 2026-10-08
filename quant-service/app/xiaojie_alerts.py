"""Feishu text for a 小杰 leader-flow research observation (潜龙出海 included).

Pure formatting, moved out of main.py (docs/decisions/0008). Research-only:
every alert says it carries zero live weight and is not a trading instruction.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from typing import Any

from .strategy_confluence import teacher_confluence_line
from .xiaojie_leader_flow import QIANLONG_EVIDENCE_LABELS


def alert_name(symbol: str, names: Mapping[str, str] | None) -> str:
    """Label a symbol the way the person reading the alert recognises it.

    A live snapshot carries no name, so an alert used to name a stock by code
    alone.  The name leads because that is what a phone notification is read
    by; the code follows so it stays copy-pasteable.  An unnamed symbol - a
    fresh listing that has not reached ``instruments`` yet - degrades to the
    bare code rather than failing the alert.
    """
    name = (names or {}).get(symbol)
    return f"{name} {symbol}" if name else symbol

def alert_text(candidate: dict[str, Any], trading_date: date,
                        names: Mapping[str, str] | None = None, *, teacher: Mapping[str, Any] | None = None,
                        chat: str | None = None) -> str:
    evidence = candidate.get("evidence") or {}
    board = evidence.get("board") or {}
    state = "封板" if board.get("sealed") else ("炸板" if board.get("broken") else "近板")
    pct = evidence.get("pct_change")
    label = alert_name(candidate["symbol"], names)
    sealed_research_notice = (
        "封板，仅作研究提醒，不追板；等待开板/承接确认。\n"
        if board.get("sealed") and candidate.get("mode") == "潜龙出海_swing" else ""
    )
    is_qianlong = candidate.get("mode") == "潜龙出海_swing"
    qianlong_line = qianlong_alert_line(evidence) if is_qianlong else ""
    warning = (evidence.get("qianlong_warning") or {}) if is_qianlong else {}
    marker = {"red": "🔴【红色预警】", "yellow": "🟡【注意】"}.get(str(warning.get("level") or ""), "")
    warning_line = (f"{marker}{'；'.join(warning.get('reasons') or [])}\n" if marker else "")
    return (
        f"{marker}【研究观察·小杰龙头】{label} {candidate.get('mode')}\n"
        f"{trading_date} {state} 涨幅 {pct:.2f}%\n" if pct is not None else
        f"{marker}【研究观察·小杰龙头】{label} {candidate.get('mode')}\n{trading_date} {state}\n"
    ) + (
        warning_line + sealed_research_notice + qianlong_line +
        f"研究仓位参考 {(candidate.get('position') or {}).get('target_fraction')}；"
        f"风险标记 {', '.join(candidate.get('risk_flags') or []) or '无'}\n"
        + (f"{teacher_confluence_line(teacher)}\n" if teacher else "")
        + (f"{chat}\n" if chat else "")
        + "仅为研究观察，零实盘权重，不构成交易指令。"
    )

def qianlong_alert_line(evidence: Mapping[str, Any]) -> str:
    """One line of the 潜龙 contract: which evidence held, overheat, and the board."""
    contract = evidence.get("qianlong_evidence") or {}
    items = contract.get("evidence") or {}
    parts: list[str] = []
    if items:
        marks = {True: "✓", False: "✗", None: "?"}
        passed = sum(1 for value in items.values() if value is True)
        parts.append(f"潜龙证据 {passed}/{len(items)}：" + " ".join(
            f"{QIANLONG_EVIDENCE_LABELS.get(name, name)}{marks.get(value, '?')}" for name, value in items.items()))
    overheat = evidence.get("qianlong_swing_overheat") or {}
    if overheat:
        text = f"过热 {int(overheat.get('count') or 0)} 项"
        if overheat.get("missing"):
            text += f"（缺 {len(overheat['missing'])} 项输入）"
        parts.append(text)
    inputs = evidence.get("qianlong_inputs") or {}
    flow = inputs.get("sector_flow") or {}
    change, rate = inputs.get("sector_day_return_pct"), inputs.get("sector_net_inflow_rate_pct")
    if flow.get("label") and change is not None:
        text = f"板块 {flow['label']} {float(change):+.2f}%"
        if rate is not None:
            text += f" 净流入率 {float(rate):+.1f}%"
        parts.append(text)
    return ("；".join(parts) + "\n") if parts else ""


__all__ = ["alert_name", "alert_text", "qianlong_alert_line"]
