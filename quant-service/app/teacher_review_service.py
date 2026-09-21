"""Teacher-review pack import and post-close roll for the watchlist strategy flow.

Flow:

1. ``import_pack`` (after the evening review video): validate, find the first
   session whose 09:15 open is after the pack became available, freeze each
   watched stock's plan from bars that end before that session, merge it into
   ``intraday_watchlists.metadata.teacher_review`` and archive the pack.
2. The intraday scan evaluates the plan (``teacher_review_rules``) and emits
   ordinary signal events, which the existing confirmation/outbox/Feishu path
   delivers.
3. ``roll`` (post-close refresh stage): settle the session's triggers and the
   teacher's forecasts, re-plan still-valid trend stocks for the next session
   and retire expired plans.

Market data comes only from evidence the data-source layer already stored
(see ``teacher_review_repository``); the strategy names capabilities in
``platform/strategy_data_needs.py`` and never calls a vendor.

Research only: no order is created and no live threshold is changed.
"""

from __future__ import annotations

import asyncio
from functools import partial
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Mapping
from zoneinfo import ZoneInfo

from . import teacher_review_repository as repo
from .teacher_review_playbooks import playbook_kind, ts_code, validate_pack
from .teacher_review_plan import DIVERGENCE_MIN_BARS, divergence_status, plan_stock
from .teacher_review_rules import MODEL_VERSION

_CN_TZ = ZoneInfo("Asia/Shanghai")
_OPEN = time(9, 15)
_STANCE_RANK = {"positive": 0, "watch": 1, "unknown": 2, "negative": 3}


@dataclass(frozen=True)
class TeacherReviewDependencies:
    database: Any
    run_database: Callable[..., Awaitable[Any]]
    now_utc: Callable[[], datetime]
    send_alert: Callable[[str], Awaitable[dict[str, Any]]]
    max_symbols: Callable[[], int]
    exchange_for: Callable[[str], str]
    hydrate_history: Callable[[Any, str], Awaitable[dict[str, Any]]] | None = None
    # Data-plane 30/60-minute K-line history (Longhu ``GetKLineDay_W14``).
    period_bars: Callable[[str, str], Awaitable[list[dict[str, Any]]]] | None = None
    # Data-plane repair of one session's daily bars and controls (Longhu first).
    repair_daily: Callable[[date], Awaitable[dict[str, Any]]] | None = None
    max_repair_dates: int = 5
    reserve: int = 5
    lookback_days: int = 14


def _db(deps: "TeacherReviewDependencies", action: Callable[..., Any], *args: Any,
        timeout_seconds: float = 30, **kwargs: Any) -> Awaitable[Any]:
    """``run_database_blocking`` forwards positional arguments only."""
    return deps.run_database(partial(action, deps.database, *args, **kwargs), timeout_seconds=timeout_seconds)


def _parse_time(value: Any) -> datetime:
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=_CN_TZ)


def _open_at(session: date) -> datetime:
    return datetime.combine(session, _OPEN, tzinfo=_CN_TZ)


def _short(value: Any, limit: int = 200) -> str:
    text = str(value or "")
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _compact_params(params: Mapping[str, Any]) -> dict[str, Any]:
    return {key: (_short(value, 80) if isinstance(value, str) else value) for key, value in params.items()}


def _priority(stock: Mapping[str, Any]) -> tuple[int, int, int]:
    kind = playbook_kind(str(stock["playbook"]))
    return (0 if kind == "relay" else 1, _STANCE_RANK.get(str(stock.get("stance")), 2), int(stock.get("_order", 0)))


def _eligible_session(sessions: list[date], available_at: datetime) -> tuple[date, int] | None:
    for index, session in enumerate(sessions, start=1):
        if _open_at(session) > available_at:
            return session, index
    return None


async def _bounded(items: list[Any], worker: Callable[[Any], Awaitable[Any]], limit: int = 2) -> list[Any]:
    gate = asyncio.Semaphore(limit)

    async def run(item: Any) -> Any:
        async with gate:
            try:
                return await worker(item)
            except Exception as error:  # noqa: BLE001 - one symbol must not abort the pack
                return error
    return await asyncio.gather(*(run(item) for item in items))


def period_bars_through(rows: list[dict[str, Any]], through: date) -> list[dict[str, Any]]:
    """Data-plane period bars ending on or before ``through`` in ``bullish_divergence`` form."""
    last = through.strftime("%Y%m%d")
    return [{"date": str(row["bar_time"]), "open": row["open"], "high": row["high"], "low": row["low"],
             "close": row["close"]} for row in rows if str(row.get("bar_time") or "")[:8] <= last]


async def period_divergence(symbol: str, period: str, through: date, deps: "TeacherReviewDependencies") -> dict[str, Any]:
    """Pre-session 30/60-minute divergence from the data plane, point-in-time at ``through``.

    Longhu period K-line first (it carries weeks of history); stored minute
    sessions only when that yields fewer bars.  Too few bars is reported as
    ``insufficient_bars`` rather than "no divergence".
    """
    bars: list[dict[str, Any]] = []
    source, error = None, None
    if deps.period_bars is not None:
        try:
            bars = period_bars_through(await deps.period_bars(symbol, period), through)
            source = "longhuvip_kline"
        except Exception as failure:  # noqa: BLE001 - a missing source degrades to stored minutes
            error = f"{type(failure).__name__}: {str(failure)[:160]}"
    if len(bars) < DIVERGENCE_MIN_BARS:
        stored = await _db(deps, repo.minute_period_bars, symbol, through=through, period=period)
        if len(stored) > len(bars):
            bars, source = stored, "intraday_minute_sessions"
    result = divergence_status(bars, source)
    if error:
        result["source_error"] = error
    return result


async def build_session_plans(
    pack: Mapping[str, Any], session: date, session_index: int, previous_session: date,
    deps: TeacherReviewDependencies,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Freeze plans for one session from stored bars that end at ``previous_session``."""
    analyst = str(pack["analyst"]["analyst_id"])
    stocks = [
        {**stock, "_order": order} for order, stock in enumerate(pack["stocks"])
        if playbook_kind(str(stock["playbook"])) != "record" and int(stock["valid_sessions"]) >= session_index
    ]
    symbols = [ts_code(str(stock["code"])) for stock in stocks]
    stored = await _db(deps, repo.plan_bars, symbols, through=previous_session, timeout_seconds=60)
    repair = await _repair_missing_sessions(stored, previous_session, deps)
    if repair.get("dates"):
        retry = [symbol for symbol in symbols if not _complete(stored.get(symbol), previous_session)]
        stored.update(await _db(deps, repo.plan_bars, retry, through=previous_session, timeout_seconds=60))
    plans, failures = [], []
    for stock in stocks:
        code = str(stock["code"])
        entry = stored.get(ts_code(code)) or {"status": "unavailable", "flags": ["no_bars"]}
        if entry["status"] != "ok" or entry["trading_date"] != previous_session or len(entry["bars"]) < 20:
            failures.append({"code": code, "name": stock.get("name"),
                             "reason": f"no adjusted bar for {previous_session} ({','.join(entry.get('flags') or ['stale'])})"})
            continue
        divergence = None
        if stock["playbook"] == "ma5_reclaim_or_divergence":
            divergence = {period: await period_divergence(ts_code(code), period, previous_session, deps)
                          for period in ("30", "60")}
        plan = plan_stock(stock, entry["bars"], divergence=divergence)
        plans.append({
            "ts_code": ts_code(code), "name": str(stock.get("name") or code),
            "label": f"{stock.get('name') or code}·复盘{str(pack['review_date'])[5:].replace('-', '')}",
            "priority": _priority(stock),
            "metadata": {
                "status": "active", "model_version": MODEL_VERSION,
                "pack_id": pack["pack_id"], "analyst_id": analyst, "review_date": pack["review_date"],
                "session_date": session.isoformat(), "session_index": session_index,
                "valid_sessions": int(stock["valid_sessions"]), "code": code, "name": stock.get("name"),
                "group": stock.get("group"), "stance": stock.get("stance"), "playbook": stock["playbook"],
                "kind": playbook_kind(str(stock["playbook"])), "params": _compact_params(stock.get("params") or {}),
                "levels": plan["levels"], "extra": plan["extra"], "setup": plan["setup"], "checklist": plan["checklist"],
                "teacher": _short(stock.get("teacher")), "invalidation": _short(stock.get("invalidation"), 120),
                "evidence_times": [str(item.get("time")) for item in stock.get("evidence") or []][:6],
                "research_only": True, "live_effect": "none",
            },
        })
    plans.sort(key=lambda item: item["priority"])
    if repair.get("dates"):
        failures.append({"code": "*", "name": "数据面补数", "reason": str(repair)[:300]})
    return plans, failures


def _complete(entry: Mapping[str, Any] | None, previous_session: date) -> bool:
    return bool(entry) and entry.get("status") == "ok" and entry.get("trading_date") == previous_session \
        and len(entry.get("bars") or []) >= 20


async def _repair_missing_sessions(stored: Mapping[str, Mapping[str, Any]], previous_session: date,
                                   deps: TeacherReviewDependencies) -> dict[str, Any]:
    """Ask the data plane to fill the sessions whose bars/factors are missing, newest first."""
    wanted: set[date] = set()
    for entry in stored.values():
        if _complete(entry, previous_session):
            continue
        for flag in entry.get("flags") or []:
            if str(flag).startswith("bar_gaps:"):
                wanted.update(date.fromisoformat(item) for item in str(flag).split(":", 1)[1].split(",") if item)
        if entry.get("status") != "ok" or entry.get("trading_date") != previous_session:
            wanted.add(previous_session)
    if not wanted or deps.repair_daily is None:
        return {"dates": [], "status": "not_needed" if not wanted else "no_repair_interface"}
    dates = sorted(wanted, reverse=True)[: deps.max_repair_dates]
    outcomes = {}
    for day in dates:
        try:
            outcomes[day.isoformat()] = await deps.repair_daily(day)
        except Exception as error:  # noqa: BLE001 - a failed repair leaves the gap reported
            outcomes[day.isoformat()] = {"status": "failed", "error": _short(error, 160)}
    return {"dates": [day.isoformat() for day in dates], "outcomes": outcomes}


async def import_pack(pack: dict[str, Any], deps: TeacherReviewDependencies, *, dry_run: bool = False) -> dict[str, Any]:
    problems = validate_pack(pack)
    if problems:
        return {"status": "rejected", "problems": problems}
    existing = await _db(deps, repo.pack_record, pack["pack_id"])
    if existing is not None and not dry_run:
        return {"status": "duplicate", "pack_id": pack["pack_id"], "first_available_at": str(existing["available_at"]),
                "import": (existing.get("payload") or {}).get("import")}
    now = deps.now_utc()
    available_at = max(_parse_time(pack["source"]["strategy_available_at"]), now)
    review_date = date.fromisoformat(str(pack["review_date"]))
    sessions = await _db(deps, repo.open_sessions, after=review_date, count=12)
    eligible = _eligible_session(sessions, available_at)
    summary: dict[str, Any] = {
        "pack_id": pack["pack_id"], "analyst_id": pack["analyst"]["analyst_id"], "review_date": pack["review_date"],
        "available_at": available_at.isoformat(), "stocks": len(pack["stocks"]),
        "watched_candidates": sum(playbook_kind(str(s["playbook"])) != "record" for s in pack["stocks"]),
        "record_only": sum(playbook_kind(str(s["playbook"])) == "record" for s in pack["stocks"]),
    }
    if eligible is None:
        summary.update({"status": "no_eligible_session", "reason": "calendar has no session after availability"})
    else:
        session, index = eligible
        previous = review_date if index == 1 else sessions[index - 2]
        plans, failures = await build_session_plans(pack, session, index, previous, deps)
        summary.update({"session_date": session.isoformat(), "session_index": index, "plan_failures": failures,
                        "planned": [plan["ts_code"] for plan in plans]})
        if dry_run:
            summary.update({"status": "dry_run", "plans": plans})
            return summary
        applied = await _db(
            deps, repo.apply_session_plans, plans, max_symbols=deps.max_symbols(), reserve=deps.reserve,
            exchange_for=deps.exchange_for, timeout_seconds=60,
        )
        summary["watchlist"] = {key: value for key, value in applied.items() if key != "new_rows"}
        summary["history_hydration"] = await _hydrate(applied.get("new_rows") or [], deps)
        if pack.get("supersedes"):
            summary["superseded"] = {"packs": list(pack["supersedes"]), **await _db(
                deps, repo.retire_plans, keep={plan["ts_code"] for plan in plans}, retired_at=deps.now_utc(),
                only_pack_ids={str(item) for item in pack["supersedes"]}, timeout_seconds=60)}
        summary["status"] = "imported"
    if dry_run:
        summary["status"] = "dry_run"
        return summary
    review_close = datetime.combine(review_date, time(15, 0), tzinfo=_CN_TZ)
    await _db(deps, repo.persist_pack, pack, review_close=review_close,
              available_at=available_at, import_summary=summary)
    summary["alert"] = await deps.send_alert(import_text(pack, summary))
    return summary


async def _hydrate(rows: list[dict[str, Any]], deps: TeacherReviewDependencies) -> dict[str, Any]:
    if deps.hydrate_history is None or not rows:
        return {"status": "skipped", "rows": len(rows)}
    results = await _bounded(rows, lambda row: deps.hydrate_history(row["watchlist_id"], row["symbol"]), limit=2)
    failed = [row["symbol"] for row, result in zip(rows, results) if isinstance(result, Exception)]
    return {"status": "completed" if not failed else "partial", "rows": len(rows), "failed": failed}


def import_text(pack: Mapping[str, Any], summary: Mapping[str, Any]) -> str:
    watch = summary.get("watchlist") or {}
    lines = [
        f"【老师复盘入池】{pack['analyst']['analyst_id']} {pack['review_date']} 复盘",
        f"生效交易日 {summary.get('session_date', '—')}｜共 {summary['stocks']} 只：盯盘 {summary['watched_candidates']}、只记录 {summary['record_only']}",
        f"观察池：新增 {len(watch.get('admitted') or [])}、合并 {len(watch.get('merged') or [])}、"
        f"超出容量 {len(watch.get('overflow') or [])}、跳过 {len(watch.get('skipped') or [])}",
    ]
    if summary.get("plan_failures"):
        lines.append("未能冻结价位：" + "、".join(f"{item['name']}({item['reason'][:30]})" for item in summary["plan_failures"][:6]))
    if watch.get("overflow"):
        lines.append("超出容量未入池：" + "、".join(watch["overflow"][:10]))
    lines.append("盘中按老师条件逐只判定，触发即推送；仅供人工复核，不构成交易指令。")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# post-close roll
# ---------------------------------------------------------------------------


def check_forecast(check: Mapping[str, Any], bars: Mapping[str, Mapping[str, Any]],
                   first_limit: Mapping[str, Any], index_close: float | None) -> tuple[Any, bool | None]:
    """Settle one declared forecast on stored session evidence (``bars`` keyed by six-digit code)."""
    kind = check.get("kind")

    def closed_at_limit(code: str) -> bool | None:
        bar = bars.get(code)
        if not bar or bar.get("limit_up_price") is None:
            return None
        return bar["close"] >= bar["limit_up_price"] - 0.005

    if kind == "race_sealed_count":
        sealed = [code for code in check.get("codes") or [] if closed_at_limit(str(code))]
        return sealed, len(sealed) >= int(check.get("min") or 0)
    if kind == "touched_limit":
        bar = bars.get(str(check.get("code")))
        if not bar or bar.get("limit_up_price") is None:
            return None, None
        return bar["high"], bar["high"] >= bar["limit_up_price"] - 0.005
    if kind == "race_loses":
        loser, winner = str(check.get("loser")), str(check.get("winner"))
        loser_sealed = closed_at_limit(loser)
        if loser_sealed is None:
            return None, None
        if not loser_sealed:
            return {"loser_closed_at_limit": False}, True
        firsts = {code: first_limit.get(code) for code in (loser, winner)}
        if not closed_at_limit(winner) or firsts[winner] is None or firsts[loser] is None:
            return {"first_limit_up": {k: str(v) for k, v in firsts.items()}}, False
        return {"first_limit_up": {k: str(v) for k, v in firsts.items()}}, firsts[winner] < firsts[loser]
    if kind == "high_above":
        bar = bars.get(str(check.get("code")))
        return (None, None) if not bar else (bar["high"], bar["high"] > float(check["level"]))
    if kind == "sector_limit_ups_at_least":
        codes = [str(code) for code in check.get("codes") or []]
        if not codes:
            return "板块口径需人工核对（未提供成分代码）", None
        sealed = [code for code in codes if closed_at_limit(code)]
        return sealed, len(sealed) >= int(check.get("min") or 0)
    if kind == "index_close_at_least":
        return index_close, None if index_close is None else index_close >= float(check["level"])
    return None, None


async def roll(trade_date: date, deps: TeacherReviewDependencies) -> dict[str, Any]:
    """Settle ``trade_date`` and prepare plans for the next session (post-close stage)."""
    records = await _db(deps, repo.recent_packs, since=trade_date - timedelta(days=deps.lookback_days))
    superseded = {str(item) for record in records for item in (record["pack"].get("supersedes") or [])}
    records = [record for record in records if str(record["pack"].get("pack_id")) not in superseded]
    if not records:
        return {"status": "skipped", "reason": "no recent teacher-review packs", "research_only": True}
    now = deps.now_utc()
    upcoming = await _db(deps, repo.open_sessions, after=trade_date, count=1)
    next_session = upcoming[0] if upcoming else None
    settlements, plans, failures = [], [], []
    for record in records:
        pack, available_at = record["pack"], _parse_time(record["available_at"])
        review_date = date.fromisoformat(str(pack["review_date"]))
        sessions = await _db(deps, repo.sessions_between, after=review_date, through=trade_date)
        first = _eligible_session(sessions, available_at) if sessions else None
        if first is not None and trade_date in sessions:
            settlements.append(await settle_pack(pack, trade_date, sessions.index(trade_date) + 1, first[1], deps))
        if next_session is None:
            continue
        next_index = len(sessions) + 1
        if _open_at(next_session) <= available_at:
            continue
        session_plans, session_failures = await build_session_plans(pack, next_session, next_index, trade_date, deps)
        plans.extend(session_plans)
        failures.extend(session_failures)
    plans.sort(key=lambda item: item["priority"])
    applied = await _db(
        deps, repo.apply_session_plans, plans, max_symbols=deps.max_symbols(), reserve=deps.reserve,
        exchange_for=deps.exchange_for, timeout_seconds=60,
    ) if plans else {"admitted": [], "merged": [], "overflow": [], "skipped": [], "new_rows": []}
    retired = await _db(deps, repo.retire_plans, keep={plan["ts_code"] for plan in plans}, retired_at=now)
    result = {
        "status": "completed", "trade_date": trade_date.isoformat(),
        "next_session": next_session.isoformat() if next_session else None,
        "settled_packs": len(settlements), "next_plans": [plan["ts_code"] for plan in plans],
        "plan_failures": failures, "watchlist": {k: v for k, v in applied.items() if k != "new_rows"},
        "retired": retired, "research_only": True, "live_effect": "none",
    }
    if settlements:
        payload = {"trade_date": trade_date.isoformat(), "packs": settlements, "roll": result, "model_version": MODEL_VERSION}
        await _db(deps, repo.persist_settlement, trade_date, payload, available_at=now)
        result["alert"] = await deps.send_alert(settlement_text(trade_date, settlements))
    return result


async def settle_pack(pack: Mapping[str, Any], trade_date: date, session_index: int, first_index: int,
                      deps: TeacherReviewDependencies) -> dict[str, Any]:
    active = [stock for stock in pack["stocks"] if first_index <= session_index <= int(stock["valid_sessions"])
              or (session_index == first_index and playbook_kind(str(stock["playbook"])) == "record")]
    forecast_codes = {str(value) for forecast in pack.get("forecasts") or []
                      for key, value in (forecast.get("check") or {}).items() if key in {"code", "loser", "winner"}}
    forecast_codes |= {str(code) for forecast in pack.get("forecasts") or []
                       for code in (forecast.get("check") or {}).get("codes") or []}
    codes = sorted({str(stock["code"]) for stock in active} | forecast_codes)
    by_symbol = await _db(deps, repo.session_bars, [ts_code(code) for code in codes] + ["000001.SH"], trade_date)
    bars = {symbol[:6]: bar for symbol, bar in by_symbol.items() if symbol != "000001.SH"}
    index_close = (by_symbol.get("000001.SH") or {}).get("close")
    first_limit = {symbol[:6]: at for symbol, at in (await _db(
        deps, repo.first_limit_up_times, [ts_code(code) for code in codes], trade_date)).items()}
    xiaojie = {symbol[:6]: modes for symbol, modes in (await _db(deps, repo.xiaojie_session_modes, trade_date)).items()}
    events = await _db(deps, repo.session_events, trade_date)
    by_code: dict[str, list[dict[str, Any]]] = {}
    for event in events:
        if (event.get("review") or {}).get("pack_id") == pack["pack_id"]:
            by_code.setdefault(str(event["symbol"])[:6], []).append(event)
    stocks = []
    for stock in active:
        code = str(stock["code"])
        bar = bars.get(code)
        stock_events = by_code.get(code, [])
        entry = next((e for e in stock_events if e["signal_type"] == "entry" and e["state"] == "confirmed"), None)
        invalid = next((e for e in stock_events if "teacher_review_invalid" in str(e["signal_key"])), None)
        entry_price = ((entry or {}).get("review") or {}).get("features", {}).get("price") if entry else None
        limit = (bar or {}).get("limit_up_price")
        stocks.append({
            "code": code, "name": stock.get("name"), "playbook": stock["playbook"], "stance": stock.get("stance"),
            "group": stock.get("group"), "kind": playbook_kind(str(stock["playbook"])),
            "bar": bar, "closed_at_limit": None if not bar or limit is None else bar["close"] >= limit - 0.005,
            "touched_limit": None if not bar or limit is None else bar["high"] >= limit - 0.005,
            "first_limit_up_at": str(first_limit[code]) if code in first_limit else None,
            "entry": None if not entry else {"at": str(entry["observed_at"]), "price": entry_price,
                                             "path": (entry.get("review") or {}).get("path")},
            "entry_to_close_pct": round((bar["close"] / entry_price - 1) * 100, 2) if bar and entry_price else None,
            "invalidated_at": str(invalid["observed_at"]) if invalid else None,
            "confluence": {"xiaojie_modes": xiaojie.get(code, [])},
            "events": len(stock_events),
        })
    forecasts = []
    for forecast in pack.get("forecasts") or []:
        check = forecast.get("check") or {}
        window = int(check.get("within_sessions") or 1)
        if not first_index <= session_index < first_index + window:
            continue
        value, hit = check_forecast(check, bars, first_limit, index_close)
        forecasts.append({"id": forecast.get("id"), "text": forecast.get("text"), "value": value, "hit": hit,
                          "session_in_window": session_index - first_index + 1, "window": window})
    confluent = [item for item in stocks if item["confluence"]["xiaojie_modes"] and item["bar"]]
    others = [item for item in stocks if not item["confluence"]["xiaojie_modes"] and item["bar"] and item["kind"] != "record"]

    def mean_pct(items: list[dict[str, Any]]) -> float | None:
        values = [item["bar"]["pct"] for item in items if item["bar"].get("pct") is not None]
        return round(sum(values) / len(values), 2) if values else None

    return {"pack_id": pack["pack_id"], "analyst_id": pack["analyst"]["analyst_id"], "review_date": pack["review_date"],
            "session_index": session_index, "stocks": stocks, "forecasts": forecasts,
            "confluence": {"count": len(confluent), "mean_pct": mean_pct(confluent),
                           "others_count": len(others), "others_mean_pct": mean_pct(others)},
            "missing_bars": sorted(set(codes) - set(bars))}


def settlement_text(trade_date: date, settlements: list[dict[str, Any]]) -> str:
    lines = [f"【老师复盘结算｜{trade_date.isoformat()}】"]
    for pack in settlements:
        lines.append(f"{pack['analyst_id']} {pack['review_date']} 复盘（第 {pack['session_index']} 个交易日）")
        marks = {True: "✔", False: "✘", None: "?"}
        if pack["forecasts"]:
            lines.append("预判：" + "；".join(f"{f['id']}{marks[f['hit']]} {str(f['text'])[:24]}" for f in pack["forecasts"]))
        triggered = [s for s in pack["stocks"] if s["entry"]]
        if triggered:
            lines.append("触发：" + "；".join(
                f"{s['name']} {str(s['entry']['at'])[11:16]}@{s['entry']['price']}→收{(s['bar'] or {}).get('close')}"
                f"({s['entry_to_close_pct']}%)" for s in triggered[:10]))
        confluent = [s for s in pack["stocks"] if s["confluence"]["xiaojie_modes"]]
        if confluent:
            stats = pack["confluence"]
            lines.append("与小杰/潜龙出海共振：" + "、".join(
                f"{s['name']}({'/'.join(s['confluence']['xiaojie_modes'])})" for s in confluent[:8])
                + f"｜共振均涨 {stats['mean_pct']}% vs 其他 {stats['others_mean_pct']}%")
        invalidated = [s["name"] for s in pack["stocks"] if s["invalidated_at"]]
        if invalidated:
            lines.append("失效：" + "、".join(invalidated[:12]))
        rejected = [s for s in pack["stocks"] if s["kind"] == "record" and s["bar"]]
        if rejected:
            lines.append("老师否定：" + "、".join(
                f"{s['name']}{s['bar']['pct']:+.1f}%{'封板' if s['closed_at_limit'] else ''}" if s["bar"].get("pct") is not None
                else f"{s['name']}—" for s in rejected[:12]))
    lines.append("研究记录，不构成交易指令。")
    return "\n".join(lines)


__all__ = [
    "TeacherReviewDependencies", "build_session_plans", "check_forecast", "import_pack", "import_text",
    "roll", "settle_pack", "settlement_text",
]
