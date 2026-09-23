"""老师复盘计划的次日结果复盘：符合预期的、没满足条件却大涨的，以及卡在哪一条。

``teacher_review_service.roll`` 结算的是"计划本身怎么走的"（触发、失效、晋级）。
这一层回答的是学习问题：

1. 哪些按预期走了 —— 触发并守住；
2. 哪些没触发却大涨了 —— 我们的条件是不是太紧，具体卡在哪一条、差多少；
3. 老师否定的票里哪些其实涨了 —— 老师这条逻辑的边界在哪。

归因不靠猜。盘中每一次扫描的规则输入都完整存在
``quant.intraday_rule_input_snapshots``（含 quote、分钟特征、当时生效的计划），
这里按时间顺序重放同一个纯函数 ``teacher_review_rules.evaluate``，数出每条
gating 条件当天被卡了多少次、最接近触发的那一刻差在哪里。进程内上下文
（板块涨停数、竞价封单）无法从快照重放，落到 ``unknown``，不计入卡点。

每天一条存档（``teacher_review_outcome``），滚动累积成按剧本的命中/漏网统计
和条件复核建议 —— 建议只描述计数，不自动改任何阈值。

Research only：不下单、不改实盘阈值、不写自选股。
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import date, datetime
from functools import partial
from typing import Any
from zoneinfo import ZoneInfo

from . import teacher_review_repository as repo
from .strategy_outcome_measures import UNBUYABLE, measure
from .teacher_review_playbooks import ts_code
from .teacher_review_rules import (
    MODEL_VERSION as RULES_VERSION,
    SnapshotTape,
    active_plan,
    evaluate,
    missing_inputs,
    scan_features,
)

MODEL_VERSION = "teacher-outcome-review-v1"
_CN_TZ = ZoneInfo("Asia/Shanghai")

#: 收盘涨幅到这里就算"本来该吃到的一波"。封板同样算，哪怕涨幅不足。
BIG_MOVE_PCT = 5.0
#: 盘中最高涨幅到这里也算机会（冲高回落的票，条件太紧同样看得出来）。
BIG_INTRADAY_PCT = 7.0
#: 触发后收盘相对入场价低于这个数算"回落"。
FADE_PCT = 0.0
#: 学习统计回看的存档份数（一个交易日一份）。
LEARNING_SESSIONS = 20
#: 一条 gating 条件在漏掉的大涨里卡到这个次数才进复核建议。
SUGGEST_MIN_BLOCKS = 3
#: 单次复盘最多重放的股票数，保护收盘任务的时间预算。
MAX_REPLAY_SYMBOLS = 80

OUTCOME_LABELS = {
    "hit": "触发并守住",
    "triggered_faded": "触发后回落",
    "missed": "未触发但大涨",
    "invalidated": "盘中失效",
    "filtered": "未触发且未涨",
    "unbuyable": "封板价触发（买不到）",
    "avoid_missed": "老师否定但大涨",
    "avoided": "老师否定且确实未涨",
    "no_data": "无当日数据",
}
#: 值得拿出来学的几类，按报告里的先后顺序。
LEARNING_OUTCOMES = ("hit", "triggered_faded", "unbuyable", "missed", "avoid_missed", "invalidated")
#: 规则要求但当时取不到的输入，报告里用人话说。
INPUT_LABELS = {
    "price": "现价", "pre_close": "昨收", "open": "开盘价", "high": "最高", "low": "最低", "amount": "成交额",
    "turnover_pct": "换手", "volume_ratio": "量比", "vwap": "分时均价", "surge": "分时量能",
    "not_falling": "5分钟走势", "book": "封板盘口",
}


def _db(deps: Any, action: Callable[..., Any], *args: Any,
        timeout_seconds: float = 30, **kwargs: Any) -> Awaitable[Any]:
    return deps.run_database(partial(action, deps.database, *args, **kwargs), timeout_seconds=timeout_seconds)


def _pct(value: Any) -> float | None:
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return None


def high_pct(bar: Mapping[str, Any] | None) -> float | None:
    """当日最高相对昨收的涨幅（冲高回落的票也要看得到机会）。"""
    if not bar or bar.get("high") is None or not bar.get("pre_close"):
        return None
    return round((float(bar["high"]) / float(bar["pre_close"]) - 1) * 100, 2)


def classify(stock: Mapping[str, Any], *, big_move_pct: float = BIG_MOVE_PCT,
             big_intraday_pct: float = BIG_INTRADAY_PCT,
             benchmark_pct: float | None = None) -> dict[str, Any]:
    """一只票当天的结果归类，附上和小杰同一套口径的收益度量。

    触发过的票按**净收益**（扣一次往返成本）判定守住还是回落；封板价触发的
    不进胜率，单列为"买不到" —— 它反映的是标记太晚，不是选错。
    """
    bar = stock.get("bar") or {}
    close_pct = _pct(bar.get("pct"))
    top_pct = high_pct(bar)
    if not bar or close_pct is None:
        return {"outcome": "no_data", "close_pct": None, "high_pct": None, "opportunity_pct": None}
    big_close = close_pct >= big_move_pct or bool(stock.get("closed_at_limit"))
    big_intraday = (top_pct is not None and top_pct >= big_intraday_pct) or bool(stock.get("touched_limit"))
    entry = stock.get("entry") or None
    measured = measure(entry, bar, stock.get("next_bar"), benchmark_pct=benchmark_pct) if entry else {}
    if str(stock.get("kind")) == "record":
        outcome = "avoid_missed" if big_close or big_intraday else "avoided"
    elif entry and measured.get("evaluable") is False:
        outcome = UNBUYABLE
    elif entry:
        net = measured.get("net_session_return_pct")
        outcome = "hit" if (net if net is not None else -1.0) >= FADE_PCT else "triggered_faded"
    elif big_close or big_intraday:
        outcome = "missed"
    elif stock.get("invalidated_at"):
        outcome = "invalidated"
    else:
        outcome = "filtered"
    return {
        "outcome": outcome, "close_pct": close_pct, "high_pct": top_pct,
        "opportunity_pct": top_pct if top_pct is not None else close_pct,
        "big_close": big_close, "big_intraday": big_intraday,
        "measures": {key: value for key, value in measured.items() if key != "reason"} or None,
    }


def _clock(observed_at: datetime) -> str:
    return observed_at.astimezone(_CN_TZ).strftime("%H:%M")


def gate_replay(symbol: str, name: str, rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """重放一只票当天的每一次扫描，数出每条 gating 条件卡了多少次。

    ``rows`` 是 ``(observed_at, inputs)`` 按时间升序，inputs 至少含 ``watch``
    和 ``quote``。计划以快照里当时生效的那一份为准（``active_plan``），所以
    被撤下或改写过的计划不会被今天的版本污染。
    """
    tape = SnapshotTape()
    blocked: Counter[str] = Counter()
    unknown: Counter[str] = Counter()
    seen: Counter[str] = Counter()
    absent: Counter[str] = Counter()
    last_value: dict[str, Any] = {}
    evaluated = entry_scans = quote_gaps = 0
    closest: dict[str, Any] | None = None
    for row in rows:
        observed_at = row["observed_at"]
        payload = row.get("inputs") or {}
        watch = payload.get("watch")
        if not isinstance(watch, Mapping):
            continue
        plan = active_plan(watch, observed_at)
        if plan is None:
            continue
        quote = payload.get("quote")
        if not isinstance(quote, Mapping) or quote.get("price") in (None, ""):
            quote_gaps += 1
            continue
        minute = payload.get("minute_features") if isinstance(payload.get("minute_features"), Mapping) else None
        previous = payload.get("previous_quote") if isinstance(payload.get("previous_quote"), Mapping) else None
        try:
            # Same order as the live scan: read the quote once to learn the
            # volume source, feed the tape, then re-read with the tape view.
            features = scan_features(symbol, quote, minute, observed_at, name, previous)
            tape.observe(symbol, observed_at, features.get("price"), features.get("volume_lot"),
                         features["sources"].get("volume_lot"))
            tape_view = tape.features(symbol, observed_at)
            if tape_view:
                features = scan_features(symbol, quote, minute, observed_at, name, previous, tape_view)
            result = evaluate(plan, features)
            # The live scan never enters on an incomplete input set; a replay
            # that skipped this would blame a condition for a data gap.
            absent_inputs = missing_inputs(str(plan["playbook"]), features)
            if absent_inputs and result["action"] == "entry":
                result = {**result, "action": "watch"}
        except Exception:  # noqa: BLE001 - 一次扫描重放不了就跳过，统计照常
            continue
        evaluated += 1
        for item in absent_inputs:
            absent[str(item)] += 1
        gating = [item for item in result["signals"] if item.get("gating", True)]
        if not gating:
            continue
        false_gates = [item for item in gating if item["pass"] is False]
        none_gates = [item for item in gating if item["pass"] is None]
        for item in gating:
            label = str(item["name"])
            seen[label] += 1
            if item["pass"] is False:
                blocked[label] += 1
                last_value[label] = item.get("value")
            elif item["pass"] is None:
                unknown[label] += 1
        if result["action"] == "entry":
            entry_scans += 1
        shortfall = len(false_gates) + len(none_gates) + (1 if absent_inputs and not (false_gates or none_gates) else 0)
        if closest is None or shortfall < closest["shortfall"]:
            closest = {
                "shortfall": shortfall, "at": _clock(observed_at), "action": result["action"],
                "price": features.get("price"), "pct": features.get("pct"),
                "blocked": [{"name": str(item["name"]), "value": item.get("value")} for item in false_gates],
                "unknown": [str(item["name"]) for item in none_gates],
                "missing_inputs": list(absent_inputs),
            }
    gates = [
        {"name": label, "blocked": blocked[label], "scans": seen[label],
         "share": round(blocked[label] / seen[label] * 100, 1) if seen[label] else None,
         "last_value": last_value.get(label)}
        for label in sorted(blocked, key=lambda key: (-blocked[key], key))
    ]
    return {
        "scans": len(rows), "evaluated": evaluated, "entry_scans": entry_scans, "quote_gaps": quote_gaps,
        "gates": gates, "closest": closest,
        "unknown_gates": [{"name": label, "scans": unknown[label]} for label in sorted(unknown, key=lambda k: -unknown[k])],
        "missing_inputs": [{"name": label, "scans": absent[label]} for label in sorted(absent, key=lambda k: -absent[k])],
        "rules_version": RULES_VERSION,
    }


def dominant_gap(replay: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """缺失的输入本身就卡住了过半的扫描时，问题在数据，不在阈值。"""
    if not replay or not replay.get("evaluated"):
        return None
    gaps = replay.get("missing_inputs") or []
    if not gaps:
        return None
    top = max(gaps, key=lambda item: int(item.get("scans") or 0))
    share = round(int(top["scans"]) / int(replay["evaluated"]) * 100, 1)
    return {**top, "share": share} if share >= 50 else None


def _blocker_line(replay: Mapping[str, Any] | None, *, live_entry: bool = False) -> str | None:
    """一句话说明这只票卡在哪，并把数据问题和阈值问题分开。"""
    if not replay or not replay.get("evaluated"):
        return None
    if replay.get("entry_scans") and not live_entry:
        closest = replay.get("closest") or {}
        return (f"重放显示 {closest.get('at') or '盘中'} 起有 {replay['entry_scans']} 次扫描满足全部条件，"
                "但盘中没有推送 —— 要查采样间隔、输入缺失或事件确认，不是条件太严")
    gap = dominant_gap(replay)
    if gap:
        return f"输入缺失：{INPUT_LABELS.get(gap['name'], gap['name'])}（全天 {gap['share']}% 的扫描无法判定）"
    gates = replay.get("gates") or []
    if not gates:
        return None
    top = gates[0]
    text = f"{top['name']}（全天 {top['share']}% 的扫描未满足"
    if top.get("last_value") not in (None, ""):
        text += f"，最后一次 {top['last_value']}"
    text += "）"
    closest = replay.get("closest") or {}
    if closest.get("at") and closest.get("shortfall") is not None:
        text += f"；最接近 {closest['at']}，还差 {closest['shortfall']} 条"
    return text


def with_delivered_entries(packs: Sequence[Mapping[str, Any]],
                           delivered: Mapping[str, Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Trust the event log over the settlement for "did this actually push?".

    The settlement read only ``state='confirmed'`` until 2026-09-23, so older
    archives call a delivered entry a miss.  Re-deriving it here keeps the
    learning history honest without re-running any roll.
    """
    out = []
    for pack in packs:
        stocks = []
        for stock in pack.get("stocks") or []:
            entry = delivered.get(str(stock.get("code")))
            if stock.get("entry") or not entry or str(stock.get("kind")) == "record":
                stocks.append(stock)
                continue
            bar = stock.get("bar") or {}
            price = _pct(entry.get("price"))
            stocks.append({**stock,
                           "entry": {"at": str(entry.get("at")), "price": price,
                                     "path": entry.get("path"), "source": "signal_events"},
                           "entry_to_close_pct": (round((float(bar["close"]) / price - 1) * 100, 2)
                                                  if price and bar.get("close") else None)})
        out.append({**pack, "stocks": stocks})
    return out


#: Kept on every archived item so the next session can complete its forward
#: returns without re-reading the settlement.
BAR_FIELDS = ("open", "high", "low", "close", "pre_close", "limit_up_price", "pct")


def review_stocks(packs: Sequence[Mapping[str, Any]],
                  replays: Mapping[str, Mapping[str, Any]],
                  *, benchmark_pct: float | None = None) -> list[dict[str, Any]]:
    """把结算里的每只票合成一条复盘记录。"""
    items: list[dict[str, Any]] = []
    for pack in packs:
        for stock in pack.get("stocks") or []:
            code = str(stock.get("code"))
            verdict = classify(stock, benchmark_pct=benchmark_pct)
            replay = replays.get(code)
            entry = stock.get("entry") or None
            items.append({
                "code": code, "name": stock.get("name"), "playbook": stock.get("playbook"),
                "kind": stock.get("kind"), "stance": stock.get("stance"), "group": stock.get("group"),
                "pack_id": pack.get("pack_id"), "review_date": pack.get("review_date"),
                "session_index": pack.get("session_index"),
                **verdict,
                "entry": None if not entry else {"at": str(entry.get("at"))[11:16], "price": entry.get("price"),
                                                 "path": entry.get("path"), "sealed": entry.get("sealed")},
                "bar": {key: (stock.get("bar") or {}).get(key) for key in BAR_FIELDS},
                "entry_to_close_pct": stock.get("entry_to_close_pct"),
                "closed_at_limit": stock.get("closed_at_limit"), "touched_limit": stock.get("touched_limit"),
                "first_limit_up_at": (str(stock.get("first_limit_up_at"))[11:16]
                                      if stock.get("first_limit_up_at") else None),
                "invalidated_at": (str(stock.get("invalidated_at"))[11:16] if stock.get("invalidated_at") else None),
                "xiaojie_modes": ((stock.get("confluence") or {}).get("xiaojie_modes") or []),
                "blocked_by": _blocker_line(replay, live_entry=bool(entry)),
                "data_gap": dominant_gap(replay),
                "replay": replay,
            })
    items.sort(key=lambda item: (LEARNING_OUTCOMES.index(item["outcome"]) if item["outcome"] in LEARNING_OUTCOMES
                                 else len(LEARNING_OUTCOMES), -(item.get("opportunity_pct") or 0)))
    return items


def _mean(values: Sequence[float]) -> float | None:
    return round(sum(values) / len(values), 2) if values else None


def learning(reports: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """按剧本累计命中与漏网，并把反复卡住大涨的条件列成复核建议。

    只描述计数和幅度，不改任何阈值；改不改由人决定。
    """
    by_playbook: dict[str, Counter[str]] = defaultdict(Counter)
    missed_pct: dict[str, list[float]] = defaultdict(list)
    hit_pct: dict[str, list[float]] = defaultdict(list)
    excess_pct: dict[str, list[float]] = defaultdict(list)
    forward_pct: dict[str, list[float]] = defaultdict(list)
    gate_blocks: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    data_gaps: Counter[str] = Counter()
    unpushed: list[dict[str, Any]] = []
    sessions: list[str] = []
    for report in reports:
        trade_date = str(report.get("trade_date") or "")
        if trade_date:
            sessions.append(trade_date)
        for item in report.get("stocks") or []:
            playbook = str(item.get("playbook") or "unknown")
            outcome = str(item.get("outcome") or "")
            by_playbook[playbook][outcome] += 1
            value = item.get("opportunity_pct")
            if outcome == "missed" and value is not None:
                missed_pct[playbook].append(float(value))
                replay = item.get("replay") or {}
                if replay.get("entry_scans") and not item.get("entry"):
                    unpushed.append({"code": item.get("code"), "name": item.get("name"), "date": trade_date,
                                     "playbook": playbook, "pct": value, "scans": replay["entry_scans"]})
                    continue
                if item.get("data_gap"):
                    data_gaps[str(item["data_gap"]["name"])] += 1
                    continue
                for gate in (replay.get("gates") or [])[:2]:
                    gate_blocks[(playbook, str(gate.get("name")))].append(
                        {"code": item.get("code"), "name": item.get("name"), "date": trade_date,
                         "pct": value, "share": gate.get("share")})
            measures = item.get("measures") or {}
            if outcome in {"hit", "triggered_faded"}:
                # Net of one round trip, and credited against the day's median,
                # so a strong tape is not mistaken for an edge.
                if measures.get("net_session_return_pct") is not None:
                    hit_pct[playbook].append(float(measures["net_session_return_pct"]))
                if measures.get("excess_session_pct") is not None:
                    excess_pct[playbook].append(float(measures["excess_session_pct"]))
                if measures.get("net_next_open_to_close_pct") is not None:
                    forward_pct[playbook].append(float(measures["net_next_open_to_close_pct"]))
    playbooks = []
    for playbook, counts in sorted(by_playbook.items(), key=lambda pair: -sum(pair[1].values())):
        total = sum(counts.values())
        # 封板价触发的既不是命中也不是失手，它反映的是标记太晚，所以不进分母。
        actionable = (total - counts.get("avoided", 0) - counts.get("avoid_missed", 0)
                      - counts.get("no_data", 0) - counts.get(UNBUYABLE, 0))
        playbooks.append({
            "playbook": playbook, "total": total,
            **{outcome: counts.get(outcome, 0) for outcome in OUTCOME_LABELS},
            "hit_rate_pct": round(counts.get("hit", 0) / actionable * 100, 1) if actionable else None,
            "missed_rate_pct": round(counts.get("missed", 0) / actionable * 100, 1) if actionable else None,
            "missed_mean_pct": _mean(missed_pct.get(playbook, [])),
            "net_mean_pct": _mean(hit_pct.get(playbook, [])),
            "excess_mean_pct": _mean(excess_pct.get(playbook, [])),
            "net_next_day_mean_pct": _mean(forward_pct.get(playbook, [])),
        })
    suggestions = []
    for (playbook, gate), cases in sorted(gate_blocks.items(), key=lambda pair: -len(pair[1])):
        if len(cases) < SUGGEST_MIN_BLOCKS:
            continue
        suggestions.append({
            "playbook": playbook, "gate": gate, "missed_cases": len(cases),
            "mean_pct": _mean([case["pct"] for case in cases]),
            "examples": [f"{case['date']} {case['name'] or case['code']} {case['pct']:+.1f}%" for case in cases[:4]],
            "note": f"{playbook} 漏掉的大涨里，「{gate}」卡了 {len(cases)} 次，建议复核这条的阈值或取值口径",
        })
    return {
        "sessions": sorted(set(sessions)), "session_count": len(set(sessions)),
        "playbooks": playbooks, "suggestions": suggestions,
        "data_gaps": [{"input": INPUT_LABELS.get(name, name), "missed_cases": count}
                      for name, count in data_gaps.most_common()],
        "unpushed": unpushed,
    }


def _entry_line(item: Mapping[str, Any]) -> str:
    """One triggered name: when, at what, and what it was actually worth."""
    entry = item.get("entry") or {}
    measures = item.get("measures") or {}
    net = measures.get("net_session_return_pct")
    excess_pct = measures.get("excess_session_pct")
    forward = measures.get("net_next_open_to_close_pct")
    parts = [f"{item['name']} {entry.get('at', '')}@{entry.get('price')}"]
    parts.append(f"净 {net:+.1f}%" if net is not None else "净 —")
    if excess_pct is not None:
        parts.append(f"超额 {excess_pct:+.1f}%")
    if forward is not None:
        parts.append(f"次日开→收 {forward:+.1f}%")
    parts.append(f"当日 {item['close_pct']:+.1f}%")
    return "，".join(parts)


def outcome_text(trade_date: date, report: Mapping[str, Any]) -> str:
    """飞书摘要：先说结果，再说卡点，最后给一条学习结论。"""
    items = list(report.get("stocks") or [])
    lines = [f"【老师计划次日复盘｜{trade_date.isoformat()}】"]
    counts = Counter(str(item["outcome"]) for item in items)
    lines.append("｜".join(f"{OUTCOME_LABELS[outcome]} {counts[outcome]}"
                           for outcome in OUTCOME_LABELS if counts.get(outcome)))

    def names(outcome: str, limit: int = 6) -> list[Mapping[str, Any]]:
        return [item for item in items if item["outcome"] == outcome][:limit]

    benchmark = report.get("benchmark_session_pct")
    if benchmark is not None:
        lines.append(f"当日全市场中位数 {benchmark:+.2f}%，以下均为扣一次往返成本后的净收益")
    for item in names("hit"):
        lines.append("✔ " + _entry_line(item))
    for item in names("triggered_faded", 4):
        lines.append("↘ " + _entry_line(item))
    for item in names(UNBUYABLE, 4):
        entry = item.get("entry") or {}
        lines.append(f"⊘ {item['name']} {entry.get('at', '')}@{entry.get('price')} 封板价触发，买不到"
                     f"（当日 {item['close_pct']:+.1f}%，不计入胜率）")
    for item in names("missed", 6):
        tail = f"｜{item['blocked_by']}" if item.get("blocked_by") else "｜无扫描留痕"
        top = f"，盘中最高 {item['high_pct']:+.1f}%" if item.get("high_pct") is not None else ""
        lines.append(f"★ 漏 {item['name']} 收 {item['close_pct']:+.1f}%{top}{tail}")
    for item in names("avoid_missed", 4):
        lines.append(f"△ 老师否定但涨 {item['name']} {item['close_pct']:+.1f}%"
                     f"（{item.get('playbook')}｜{item.get('stance')}）")
    learned = report.get("learning") or {}
    unpushed = [item for item in (learned.get("unpushed") or []) if item.get("date") == trade_date.isoformat()]
    if unpushed:
        lines.append("待查：" + "、".join(f"{item['name']}（重放满足 {item['scans']} 次却没推送）"
                                          for item in unpushed[:4]))
    for gap in (learned.get("data_gaps") or [])[:1]:
        lines.append(f"数据缺口：{gap['input']} 缺失导致 {gap['missed_cases']} 次漏判")
    for suggestion in (learned.get("suggestions") or [])[:2]:
        lines.append(f"学习：{suggestion['note']}（近 {learned.get('session_count', 0)} 份复盘，"
                     f"这些票平均 {suggestion['mean_pct']:+.1f}%）")
    lines.append("研究记录，不构成交易指令。")
    return "\n".join(lines)


def _signed(value: Any) -> str:
    return "—" if value is None else f"{float(value):+.1f}%"


def _rate(value: Any) -> str:
    return "—" if value is None else f"{float(value):.1f}%"


def outcome_markdown(trade_date: date, report: Mapping[str, Any]) -> str:
    """人读的完整版：每只票一行，附卡点与重放证据。"""
    items = list(report.get("stocks") or [])
    lines = [f"# 老师计划次日复盘 {trade_date.isoformat()}", "",
             f"模型 `{report.get('model_version')}`｜规则 `{report.get('rules_version')}`｜"
             f"结算包 {report.get('packs')} 个｜标的 {len(items)} 只", "",
             f"收益口径与小杰结算一致：扣一次往返成本的**净收益**，超额 = 净收益 − 当日全市场中位数"
             f"（{_signed(report.get('benchmark_session_pct'))}）。封板价触发的单列为「买不到」，不计入胜率。", ""]
    counts = Counter(str(item["outcome"]) for item in items)
    lines += ["## 结果分布", "", "| 结果 | 只数 |", "|---|---|"]
    lines += [f"| {OUTCOME_LABELS[outcome]} | {counts[outcome]} |" for outcome in OUTCOME_LABELS if counts.get(outcome)]
    lines.append("")
    for outcome in OUTCOME_LABELS:
        group = [item for item in items if item["outcome"] == outcome]
        if not group:
            continue
        lines += [f"## {OUTCOME_LABELS[outcome]}（{len(group)}）", "",
                  "| 代码 | 名称 | 剧本 | 收盘 | 最高 | 触发 | 净收益 | 超额 | 次日开→收 | 卡点 |",
                  "|---|---|---|---|---|---|---|---|---|---|"]
        for item in group:
            entry = item.get("entry") or {}
            measures = item.get("measures") or {}
            trigger = f"{entry.get('at')}@{entry.get('price')}" if entry else "—"
            lines.append(
                f"| {item['code']} | {item.get('name') or ''} | {item.get('playbook')} | "
                f"{_signed(item.get('close_pct'))} | {_signed(item.get('high_pct'))} | {trigger} | "
                f"{_signed(measures.get('net_session_return_pct'))} | "
                f"{_signed(measures.get('excess_session_pct'))} | "
                f"{_signed(measures.get('net_next_open_to_close_pct'))} | {item.get('blocked_by') or '—'} |")
        lines.append("")
    learned = report.get("learning") or {}
    if learned.get("playbooks"):
        lines += [f"## 按剧本累计（近 {learned.get('session_count', 0)} 个交易日）", "",
                  "| 剧本 | 合计 | 守住 | 回落 | 买不到 | 漏掉大涨 | 命中率 | 漏网率 | 净收益均值 | 超额均值 | 漏掉幅度 |",
                  "|---|---|---|---|---|---|---|---|---|---|---|"]
        for row in learned["playbooks"]:
            lines.append(
                f"| {row['playbook']} | {row['total']} | {row['hit']} | {row['triggered_faded']} | "
                f"{row.get(UNBUYABLE, 0)} | {row['missed']} | {_rate(row['hit_rate_pct'])} | "
                f"{_rate(row['missed_rate_pct'])} | {_signed(row.get('net_mean_pct'))} | "
                f"{_signed(row.get('excess_mean_pct'))} | {_signed(row['missed_mean_pct'])} |")
        lines.append("")
    if learned.get("unpushed"):
        lines += ["## 重放满足条件却没推送（先查这里，不是阈值问题）", ""]
        for item in learned["unpushed"]:
            lines.append(f"- {item['date']} {item['name']}（{item['code']}）{item['playbook']}："
                         f"{item['scans']} 次扫描满足全部条件，当日 {item['pct']:+.1f}%")
        lines.append("")
    if learned.get("data_gaps"):
        lines += ["## 输入缺失导致的漏判", "", "| 缺的输入 | 次数 |", "|---|---|"]
        lines += [f"| {gap['input']} | {gap['missed_cases']} |" for gap in learned["data_gaps"]]
        lines.append("")
    if learned.get("suggestions"):
        lines += ["## 条件复核建议（只描述计数，改不改由人定）", ""]
        for suggestion in learned["suggestions"]:
            lines.append(f"- **{suggestion['playbook']} ·「{suggestion['gate']}」**：漏掉 "
                         f"{suggestion['missed_cases']} 次，平均 {suggestion['mean_pct']:+.1f}%；"
                         f"例：{'、'.join(suggestion['examples'])}")
        lines.append("")
    lines += ["---", "", "研究记录，不构成交易指令。"]
    return "\n".join(lines)


async def complete_previous(previous: Sequence[Mapping[str, Any]], trade_date: date, deps: Any) -> int:
    """Fill the forward returns of the last session's report with this session's bars.

    A report written at its own close cannot know the next open or close, and
    those are the only numbers an account could have earned when the flagged
    price was unavailable.  The next session's review completes it in place -
    the archive keeps the newest payload per session.
    """
    pending = next((dict(item) for item in previous
                    if any((stock.get("entry") and not (stock.get("measures") or {}).get("next_open_to_close_pct"))
                           for stock in item.get("stocks") or [])), None)
    if pending is None:
        return 0
    codes = [str(stock["code"]) for stock in pending.get("stocks") or [] if stock.get("entry")]
    if not codes:
        return 0
    bars = await _db(deps, repo.session_bars, [ts_code(code) for code in codes], trade_date)
    next_bars = {symbol[:6]: bar for symbol, bar in bars.items()}
    filled, stocks = 0, []
    for stock in pending.get("stocks") or []:
        entry, next_bar = stock.get("entry"), next_bars.get(str(stock.get("code")))
        if not entry or not next_bar:
            stocks.append(stock)
            continue
        measured = measure(entry, stock.get("bar") or {}, next_bar,
                           benchmark_pct=pending.get("benchmark_session_pct"))
        stocks.append({**stock, "measures": {key: value for key, value in measured.items() if key != "reason"}})
        filled += 1
    if not filled:
        return 0
    session = date.fromisoformat(str(pending["trade_date"]))
    payload = {**pending, "stocks": stocks, "forward_completed_on": trade_date.isoformat()}
    await _db(deps, repo.persist_outcome_review, session, payload, available_at=deps.now_utc())
    return filled


async def run(trade_date: date, deps: Any, *, alert: bool = True) -> dict[str, Any]:
    """T 日收盘后的结果复盘。读 roll 刚写的结算存档，不重算行情。"""
    settlements = await _db(deps, repo.recent_settlements, limit=LEARNING_SESSIONS + 4)
    today = next((row for row in settlements
                  if str((row["payload"] or {}).get("trade_date")) == trade_date.isoformat()), None)
    if today is None:
        return {"status": "skipped", "reason": "no settlement archived for this session",
                "trade_date": trade_date.isoformat(), "research_only": True, "live_effect": "none"}
    delivered = await _db(deps, repo.delivered_entries, trade_date)
    packs = with_delivered_entries(list((today["payload"] or {}).get("packs") or []), delivered)
    replay_codes = [
        str(stock.get("code")) for pack in packs for stock in (pack.get("stocks") or [])
        if str(stock.get("kind")) != "record" and not stock.get("entry")
    ][:MAX_REPLAY_SYMBOLS]
    names = {str(stock.get("code")): str(stock.get("name") or "")
             for pack in packs for stock in (pack.get("stocks") or [])}
    rows = await _db(deps, repo.rule_input_snapshots, replay_codes, trade_date,
                     timeout_seconds=90) if replay_codes else {}
    replays = {code: gate_replay(ts_code(code), names.get(code, ""), rows.get(code) or [])
               for code in replay_codes if rows.get(code)}
    benchmark_pct = await _db(deps, repo.session_benchmark, trade_date)
    stocks = review_stocks(packs, replays, benchmark_pct=benchmark_pct)
    report: dict[str, Any] = {
        "trade_date": trade_date.isoformat(), "model_version": MODEL_VERSION, "rules_version": RULES_VERSION,
        "packs": len(packs), "stocks": stocks,
        "delivered_entries": len(delivered), "benchmark_session_pct": benchmark_pct,
        "counts": dict(Counter(str(item["outcome"]) for item in stocks)),
        "thresholds": {"big_move_pct": BIG_MOVE_PCT, "big_intraday_pct": BIG_INTRADAY_PCT,
                       "fade_pct": FADE_PCT, "suggest_min_blocks": SUGGEST_MIN_BLOCKS},
        "research_only": True, "live_effect": "none",
    }
    history = await _db(deps, repo.recent_outcome_reviews, limit=LEARNING_SESSIONS)
    previous = [row["payload"] for row in history
                if str((row["payload"] or {}).get("trade_date")) != trade_date.isoformat()]
    completed = await complete_previous(previous, trade_date, deps)
    report["learning"] = learning([report, *previous])
    await _db(deps, repo.persist_outcome_review, trade_date, report, available_at=deps.now_utc())
    result = {"status": "completed", "trade_date": trade_date.isoformat(), "counts": report["counts"],
              "replayed": len(replays), "suggestions": len(report["learning"]["suggestions"]),
              "completed_forward_returns": completed,
              "research_only": True, "live_effect": "none"}
    if alert and stocks:
        result["alert"] = await deps.send_alert(outcome_text(trade_date, report))
    return result


__all__ = [
    "BIG_INTRADAY_PCT", "BIG_MOVE_PCT", "LEARNING_SESSIONS", "MODEL_VERSION", "OUTCOME_LABELS",
    "classify", "complete_previous", "dominant_gap", "gate_replay", "high_pct", "learning",
    "outcome_markdown", "outcome_text",
    "review_stocks", "run", "with_delivered_entries",
]
