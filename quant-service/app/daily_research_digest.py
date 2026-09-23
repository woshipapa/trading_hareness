"""One page per session: how every strategy did, and what to do about it.

The pieces already existed and each was read in a different place - the
teacher roll's Feishu line, the outcome review's markdown in a harness job
directory, the 小杰 settlement in a table, the watch-list review in another.
Reviewing the day meant opening four things and holding the comparison in
your head, so in practice it did not happen.

This merges them into one message and one page, in the order a person
actually needs them:

1. what needs a decision - the biggest miss, entries that could not be
   taken, and any change that moved against the expectation it was shipped
   with;
2. the per-strategy scoreboard, on one measurement (see
   ``strategy_outcome_measures``) so the families are comparable;
3. what accumulated - rolling rates, review notes, and the change ledger.

It computes nothing of its own: every number here is read from an archive
written by the stage that owns it, so the digest can never disagree with
the report it summarizes.

Research only.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import date, timedelta
from functools import partial
from typing import Any

from . import strategy_change_log as change_log
from . import teacher_review_repository as repo
from .strategy_outcome_measures import UNBUYABLE
from .teacher_outcome_review import OUTCOME_LABELS
from .xiaojie_outcome_settlement import mode_scorecard

MODEL_VERSION = "daily-research-digest-v1"
#: How far the rolling 小杰 scoreboard looks back.
SCORECARD_SESSIONS = 20
#: A mode with fewer evaluable entries than this is shown but never ranked.
MIN_OBSERVATIONS = 3


def _db(deps: Any, action: Callable[..., Any], *args: Any,
        timeout_seconds: float = 30, **kwargs: Any) -> Awaitable[Any]:
    return deps.run_database(partial(action, deps.database, *args, **kwargs), timeout_seconds=timeout_seconds)


def xiaojie_scorecard(database: Any, start_date: date, end_date: date) -> list[dict[str, Any]]:
    with database.transaction() as connection:
        return mode_scorecard(connection, start_date, end_date)


def decisions(teacher: Mapping[str, Any] | None, changes: Sequence[Mapping[str, Any]]) -> list[str]:
    """The few lines that should change what someone does tomorrow."""
    out: list[str] = []
    stocks = (teacher or {}).get("stocks") or []
    missed = [item for item in stocks if item.get("outcome") == "missed"]
    if missed:
        worst = max(missed, key=lambda item: item.get("opportunity_pct") or 0)
        out.append(f"最大漏网：{worst.get('name')} {worst.get('opportunity_pct'):+.1f}%"
                   f"｜{worst.get('blocked_by') or '无扫描留痕'}")
    unbuyable = [item for item in stocks if item.get("outcome") == UNBUYABLE]
    if unbuyable:
        out.append(f"买不到的触发 {len(unbuyable)} 只（" + "、".join(
            str(item.get("name")) for item in unbuyable[:4]) + "）：买点设计太晚，不是阈值问题")
    defects = [item for item in ((teacher or {}).get("learning") or {}).get("unpushed", [])
               if item.get("date") == (teacher or {}).get("trade_date")]
    if defects:
        out.append("重放满足却没推送 " + "、".join(f"{item['name']}×{item['scans']}" for item in defects[:3])
                   + "：先查系统，别放宽条件")
    against = [item for item in changes if item.get("verdict") == "against_expectation"]
    for item in against[:2]:
        out.append(f"改动与预期相反：{item['parameter']} {item['from']}→{item['to']}"
                   f"（{item['measure']} 期望 {item['direction']}，实际 {item['moved']:+.2f}）")
    return out


def build(trade_date: date, *, teacher: Mapping[str, Any] | None,
          xiaojie: Sequence[Mapping[str, Any]], changes: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """One session's digest, assembled only from what the owners archived."""
    counts = (teacher or {}).get("counts") or {}
    learned = (teacher or {}).get("learning") or {}
    ranked = [row for row in xiaojie if int(row.get("observations") or 0) >= MIN_OBSERVATIONS]
    return {
        "trade_date": trade_date.isoformat(), "model_version": MODEL_VERSION,
        "decisions": decisions(teacher, changes),
        "teacher": {
            "counts": counts,
            "benchmark_session_pct": (teacher or {}).get("benchmark_session_pct"),
            "session_count": learned.get("session_count"),
            "playbooks": learned.get("playbooks") or [],
            "suggestions": learned.get("suggestions") or [],
            "present": teacher is not None,
        },
        "xiaojie": {"modes": list(xiaojie), "ranked": ranked, "sessions": SCORECARD_SESSIONS},
        "changes": list(changes),
        "research_only": True, "live_effect": "none",
    }


def _pct(value: Any, digits: int = 1) -> str:
    return "—" if value is None else f"{float(value):+.{digits}f}%"


def _rate(value: Any) -> str:
    return "—" if value is None else f"{float(value):.0f}%"


def digest_text(trade_date: date, digest: Mapping[str, Any]) -> str:
    """The Feishu message: decisions first, scoreboard second."""
    lines = [f"【每日研究复盘｜{trade_date.isoformat()}】"]
    for line in digest.get("decisions") or []:
        lines.append("▶ " + line)
    teacher = digest.get("teacher") or {}
    if teacher.get("present"):
        counts = teacher.get("counts") or {}
        lines.append("老师计划：" + "｜".join(f"{OUTCOME_LABELS.get(key, key)} {value}"
                                             for key, value in counts.items()))
    ranked = (digest.get("xiaojie") or {}).get("ranked") or []
    for row in sorted(ranked, key=lambda item: -(item.get("avg_excess_pct") or -99))[:3]:
        lines.append(f"小杰·{row['mode']}：{row['observations']} 次，净 {_pct(row.get('avg_net_session_pct'), 2)}"
                     f"，超额 {_pct(row.get('avg_excess_pct'), 2)}"
                     f"，胜率 {_rate(row.get('session_win_pct'))}")
    for line in change_log.change_lines(digest.get("changes") or [])[:2]:
        lines.append("改动跟踪：" + line)
    lines.append("研究记录，不构成交易指令。")
    return "\n".join(lines)


def digest_markdown(trade_date: date, digest: Mapping[str, Any]) -> str:
    """The page: everything above with the tables behind it."""
    lines = [f"# 每日研究复盘 {trade_date.isoformat()}", "",
             f"模型 `{digest.get('model_version')}`。收益一律是扣一次往返成本的净值，"
             "超额 = 净收益 − 当日全市场中位数，封板价触发的不计入胜率。", ""]
    if digest.get("decisions"):
        lines += ["## 需要决定的", ""] + [f"- {line}" for line in digest["decisions"]] + [""]
    teacher = digest.get("teacher") or {}
    if teacher.get("present"):
        counts = teacher.get("counts") or {}
        lines += ["## 老师计划", "", "| 结果 | 只数 |", "|---|---|"]
        lines += [f"| {OUTCOME_LABELS.get(key, key)} | {value} |" for key, value in counts.items()]
        lines += ["", f"当日全市场中位数 {_pct(teacher.get('benchmark_session_pct'), 2)}"
                      f"；滚动样本 {teacher.get('session_count') or 0} 个交易日。", ""]
        if teacher.get("playbooks"):
            lines += ["| 剧本 | 样本 | 守住 | 回落 | 买不到 | 漏掉 | 命中率 | 净收益 | 超额 |",
                      "|---|---|---|---|---|---|---|---|---|"]
            for row in teacher["playbooks"]:
                rate = row.get("hit_rate_pct")
                lines.append(f"| {row['playbook']} | {row['total']} | {row.get('hit', 0)} | "
                             f"{row.get('triggered_faded', 0)} | {row.get(UNBUYABLE, 0)} | "
                             f"{row.get('missed', 0)} | {_rate(rate)} | "
                             f"{_pct(row.get('net_mean_pct'))} | {_pct(row.get('excess_mean_pct'))} |")
            lines.append("")
    modes = (digest.get("xiaojie") or {}).get("modes") or []
    if modes:
        lines += [f"## 小杰（近 {(digest.get('xiaojie') or {}).get('sessions')} 个交易日，已剔除封板时标记的）", "",
                  "| 模式 | 观测 | 已告警 | 净收益 | 超额 | 胜率 | 次日开→收(净) |",
                  "|---|---|---|---|---|---|---|"]
        for row in modes:
            win = row.get("session_win_pct")
            lines.append(f"| {row['mode']} | {row['observations']} | {row.get('alerted', 0)} | "
                         f"{_pct(row.get('avg_net_session_pct'), 2)} | {_pct(row.get('avg_excess_pct'), 2)} | "
                         f"{_rate(win)} | {_pct(row.get('avg_net_next_open_to_close_pct'), 2)} |")
        lines.append("")
    if digest.get("changes"):
        lines += ["## 改动跟踪（对照改动前写下的预期）", ""]
        lines += [f"- {line}" for line in change_log.change_lines(digest["changes"])] + [""]
    suggestions = (digest.get("teacher") or {}).get("suggestions") or []
    if suggestions:
        lines += ["## 条件复核建议（只描述计数，改不改由人定）", ""]
        lines += [f"- **{item['playbook']} ·「{item['gate']}」**：漏掉 {item['missed_cases']} 次，"
                  f"平均 {_pct(item['mean_pct'])}" for item in suggestions] + [""]
        lines.append("> 要改就先用 `teacher_review_ops sweep` 量一遍两边的代价，再把改动连同预期写进变更台账。")
        lines.append("")
    lines += ["---", "", "研究记录，不构成交易指令。"]
    return "\n".join(lines)


async def run(trade_date: date, deps: Any, *, alert: bool = True) -> dict[str, Any]:
    """Assemble and deliver one session's digest."""
    reports = await _db(deps, repo.recent_outcome_reviews, limit=change_log.DEFAULT_REVIEW_SESSIONS + 5)
    payloads = [row["payload"] for row in reports if row.get("payload")]
    teacher = next((item for item in payloads
                    if str(item.get("trade_date")) == trade_date.isoformat()), None)
    scorecard = await _db(deps, xiaojie_scorecard, trade_date - timedelta(days=SCORECARD_SESSIONS * 2),
                          trade_date, timeout_seconds=60)
    recorded = await _db(deps, change_log.recent, limit=50)
    digest = build(trade_date, teacher=teacher, xiaojie=scorecard,
                   changes=change_log.evaluate(recorded, payloads))
    result = {"status": "completed", "trade_date": trade_date.isoformat(),
              "decisions": len(digest["decisions"]), "modes": len(scorecard),
              "teacher_present": digest["teacher"]["present"],
              "research_only": True, "live_effect": "none"}
    if alert and (digest["decisions"] or digest["teacher"]["present"] or scorecard):
        result["alert"] = await deps.send_alert(digest_text(trade_date, digest))
    result["digest"] = digest
    return result


__all__ = [
    "MIN_OBSERVATIONS", "MODEL_VERSION", "SCORECARD_SESSIONS", "build", "decisions",
    "digest_markdown", "digest_text", "run", "xiaojie_scorecard",
]
