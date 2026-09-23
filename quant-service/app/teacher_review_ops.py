"""Operator commands for the daily teacher-review cycle, run inside the peer.

    python -m app.teacher_review_ops context --date 2026-09-22
    python -m app.teacher_review_ops check  --pack -        < pack.json
    python -m app.teacher_review_ops import --pack -        < pack.json
    python -m app.teacher_review_ops status
    python -m app.teacher_review_ops outcome --date 2026-09-22
    python -m app.teacher_review_ops sweep   --date 2026-09-22
    python -m app.teacher_review_ops digest  --date 2026-09-22

Each prints one JSON document on stdout.  ``context``, ``check`` and
``status`` only read.  ``import`` goes through the service's own HTTP route,
so the lifespan-owned provider clients, the Feishu transport and the
write-key check all apply; the key is read from this container's environment
and never printed.  The workstation driver is
``scripts/teacher_review_daily.py``; the procedure is
``docs/TEACHER_REVIEW_DAILY.md``.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable, Mapping
from zoneinfo import ZoneInfo

from .teacher_outcome_review import OUTCOME_LABELS
from .teacher_review_lifecycle import STATE_LABELS
from .teacher_review_playbooks import playbook_kind, ts_code, validate_pack

_CN = ZoneInfo("Asia/Shanghai")
PEER_CLOSE_STAGES = ("teacher_review_roll", "teacher_outcome_review", "watch_daily_review", "xiaojie_outcomes")
IMPORT_URL = "http://127.0.0.1:8000/api/v1/teacher-review/packs"


def pack_digest(pack: Mapping[str, Any]) -> str:
    """The pack-id convention: sha256 of the canonical JSON without ``pack_id``, 16 hex."""
    body = {key: value for key, value in pack.items() if key != "pack_id"}
    return hashlib.sha256(json.dumps(body, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16]


def _plan_view(symbol: str, plan: Mapping[str, Any]) -> dict[str, Any]:
    lifecycle = plan.get("lifecycle") or {}
    params = plan.get("params") or {}
    return {
        "symbol": symbol, "name": plan.get("name"), "session_date": plan.get("session_date"),
        "status": plan.get("status"), "state": lifecycle.get("state") or "new",
        "reason": lifecycle.get("reason"), "since": lifecycle.get("since"), "carried": lifecycle.get("carried"),
        "playbook": plan.get("playbook"), "original_playbook": lifecycle.get("original_playbook"),
        "pack_id": plan.get("pack_id"), "review_date": plan.get("review_date"),
        "key_level": params.get("prior_high") or params.get("support_level") or params.get("neckline"),
        "setup": plan.get("setup"),
    }


def pool_view(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Enabled teacher plans in the watch pool, one per stock."""
    out = []
    for row in rows:
        plan = (row.get("metadata") or {}).get("teacher_review")
        if row.get("enabled") and isinstance(plan, Mapping):
            out.append(_plan_view(str(row["symbol"]).upper(), plan))
    return sorted(out, key=lambda item: ({"new": 0, "promoted": 1, "observe": 2}.get(item["state"], 3), item["symbol"]))


def outcome_digest(outcome: Mapping[str, Any] | None) -> dict[str, Any]:
    """The part of the outcome review a pack builder must act on.

    Counting what our own conditions missed is only useful if it reaches the
    next decision, so the digest travels with the context rather than sitting
    in an archive nobody opens.
    """
    if not outcome:
        return {}
    learned = outcome.get("learning") or {}
    stocks = outcome.get("stocks") or []
    return {
        "trade_date": outcome.get("trade_date"),
        "counts": outcome.get("counts") or {},
        "benchmark_session_pct": outcome.get("benchmark_session_pct"),
        "missed": [{"code": item.get("code"), "name": item.get("name"), "playbook": item.get("playbook"),
                    "opportunity_pct": item.get("opportunity_pct"), "blocked_by": item.get("blocked_by")}
                   for item in stocks if item.get("outcome") == "missed"],
        "unbuyable": [{"code": item.get("code"), "name": item.get("name"),
                       "at": (item.get("entry") or {}).get("at")}
                      for item in stocks if item.get("outcome") == "unbuyable"],
        "rejected_but_ran": [{"code": item.get("code"), "name": item.get("name"),
                              "close_pct": item.get("close_pct")}
                             for item in stocks if item.get("outcome") == "avoid_missed"],
        "session_count": learned.get("session_count"),
        "playbooks": [row for row in (learned.get("playbooks") or []) if row.get("total", 0) >= 2][:10],
        "suggestions": learned.get("suggestions") or [],
        "data_gaps": learned.get("data_gaps") or [],
        "unpushed": learned.get("unpushed") or [],
    }


def build_context(trade_date: date, *, settlement: Mapping[str, Any] | None, rows: Iterable[Mapping[str, Any]],
                  packs: Iterable[Mapping[str, Any]], bars: Mapping[str, Mapping[str, Any]],
                  receipts: Mapping[str, str], outcome: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Everything a pack builder needs about the session that just closed."""
    pool = pool_view(rows)
    roll = (settlement or {}).get("roll") or {}
    packs_out = []
    for pack in (settlement or {}).get("packs") or []:
        packs_out.append({
            "pack_id": pack.get("pack_id"), "review_date": pack.get("review_date"),
            "session_index": pack.get("session_index"),
            "forecasts": [{"id": item.get("id"), "text": item.get("text"), "hit": item.get("hit"),
                           "value": item.get("value")} for item in pack.get("forecasts") or []],
            "triggered": [{"code": item["code"], "name": item.get("name"), "entry": item.get("entry"),
                           "entry_to_close_pct": item.get("entry_to_close_pct")}
                          for item in pack.get("stocks") or [] if item.get("entry")],
            "rejected_results": [{"code": item["code"], "name": item.get("name"),
                                  "pct": (item.get("bar") or {}).get("pct"), "sealed": item.get("closed_at_limit")}
                                 for item in pack.get("stocks") or [] if item.get("kind") == "record" and item.get("bar")],
        })
    return {
        "trade_date": trade_date.isoformat(),
        "next_session": roll.get("next_session"),
        "peer_close_stages": dict(receipts),
        "settled": settlement is not None,
        "lifecycle": roll.get("lifecycle") or [],
        "packs": packs_out,
        "active_packs": [{"pack_id": item["pack_id"], "review_date": item["review_date"],
                          "available_at": item["available_at"], "stocks": item["stocks"]} for item in packs],
        "pool": pool,
        "close": {symbol: {key: bar.get(key) for key in ("close", "pct", "high", "low", "amount", "limit_up_price")}
                  for symbol, bar in bars.items()},
        "outcome": outcome_digest(outcome),
        "research_only": True,
    }


def context_markdown(context: Mapping[str, Any]) -> str:
    """The same context as a page a person or an agent reads before building the pack."""
    lines = [f"# 老师复盘每日上下文 · {context['trade_date']}", ""]
    stages = context.get("peer_close_stages") or {}
    lines.append("收盘研究阶段：" + "，".join(f"{name}={stages.get(name, 'missing')}" for name in PEER_CLOSE_STAGES))
    if not context.get("settled"):
        lines.append("")
        lines.append("> 当日结算尚未生成，先确认 `peer_close_research` 已跑完（16:15 之后）再生成策略包。")
    for pack in context.get("packs") or []:
        lines += ["", f"## 旧复盘 {pack['review_date']}（{pack['pack_id']}，第 {pack['session_index']} 个交易日）"]
        marks = {True: "✔", False: "✘", None: "?"}
        for item in pack["forecasts"]:
            lines.append(f"- 预判 {item['id']}{marks.get(item['hit'], '?')} {item['text']}")
        for item in pack["triggered"]:
            lines.append(f"- 触发 {item['name']}（{item['code']}）→ 收盘 {item['entry_to_close_pct']}%")
        if pack["rejected_results"]:
            lines.append("- 老师否定的票当日表现：" + "、".join(
                f"{item['name']}{item['pct']:+.1f}%{'封板' if item['sealed'] else ''}" if item["pct"] is not None
                else f"{item['name']}—" for item in pack["rejected_results"]))
    outcome = context.get("outcome") or {}
    if outcome:
        counts = outcome.get("counts") or {}
        lines += ["", f"## 昨日复盘：我们自己的条件表现（{outcome.get('trade_date')}）", "",
                  "、".join(f"{OUTCOME_LABELS.get(key, key)} {value}" for key, value in counts.items()) or "—"]
        if outcome.get("benchmark_session_pct") is not None:
            lines.append(f"当日全市场中位数 {outcome['benchmark_session_pct']:+.2f}%；"
                         "收益均为扣一次往返成本后的净值，封板价触发的不计入胜率。")
        if outcome.get("missed"):
            lines += ["", "**没触发却大涨的（条件可能太紧）**", ""]
            lines += [f"- {item['name']}（{item['code']}）{item['playbook']} "
                      f"{item['opportunity_pct']:+.1f}%｜{item.get('blocked_by') or '无扫描留痕'}"
                      for item in outcome["missed"]]
        if outcome.get("unbuyable"):
            lines += ["", "**封板价才触发（等于没抓到，说明标记太晚）**：" + "、".join(
                f"{item['name']} {item.get('at') or ''}" for item in outcome["unbuyable"])]
        if outcome.get("rejected_but_ran"):
            lines += ["", "**老师否定却大涨的**：" + "、".join(
                f"{item['name']} {item['close_pct']:+.1f}%" for item in outcome["rejected_but_ran"])]
        for suggestion in (outcome.get("suggestions") or [])[:3]:
            lines += ["", f"> 复核建议：{suggestion['note']}（近 {outcome.get('session_count')} 份复盘，"
                          f"这些票平均 {suggestion['mean_pct']:+.1f}%）"]
        if outcome.get("unpushed"):
            lines += ["", "**重放满足条件却没推送（缺陷，别据此放宽阈值）**：" + "、".join(
                f"{item['name']}×{item['scans']}" for item in outcome["unpushed"])]
        if outcome.get("playbooks"):
            lines += ["", "| 剧本 | 样本 | 守住 | 回落 | 漏掉 | 命中率 | 净收益均值 |", "|---|---|---|---|---|---|---|"]
            for row in outcome["playbooks"]:
                rate = row.get("hit_rate_pct")
                net = row.get("net_mean_pct")
                lines.append(f"| {row['playbook']} | {row['total']} | {row.get('hit', 0)} | "
                             f"{row.get('triggered_faded', 0)} | {row.get('missed', 0)} | "
                             f"{'—' if rate is None else f'{rate:.0f}%'} | "
                             f"{'—' if net is None else f'{net:+.1f}%'} |")
        lines.append("")
        lines.append("建包时用得上：某个剧本反复漏掉大涨，就在参数里放宽对应的条件并写明理由；"
                     "反复出现封板价才触发，说明这个剧本的买点设计本身要改。")

    lifecycle = context.get("lifecycle") or []
    if lifecycle:
        lines += ["", "## 计划延续结果（系统按收盘判定）", "", "| 股票 | 前状态 | 结果 | 原因 |", "|---|---|---|---|"]
        for item in lifecycle:
            lines.append(f"| {item.get('name') or item['code']}（{item['code']}） | {STATE_LABELS.get(item.get('from'), item.get('from'))} "
                         f"| {STATE_LABELS.get(item['state'], item['state'])} | {item['reason']} |")
    pool = context.get("pool") or []
    lines += ["", f"## 下一交易日 {context.get('next_session') or '—'} 已在观察池的老师计划（{len(pool)} 只）", "",
              "| 股票 | 状态 | 剧本 | 关键价 | 来源复盘 | 说明 |", "|---|---|---|---|---|---|"]
    close = context.get("close") or {}
    for item in pool:
        bar = close.get(item["symbol"]) or {}
        pct = f"{bar['pct']:+.2f}%" if bar.get("pct") is not None else ""
        lines.append(f"| {item['name']}（{item['symbol']}）{pct} | {STATE_LABELS.get(item['state'], item['state'])} "
                     f"| {item['playbook']} | {item['key_level'] or ''} | {item['review_date']} | {item['reason'] or item['setup'] or ''} |")
    lines += ["", "新复盘里老师点评到的票，一律以新复盘为准（覆盖上表）；新复盘没提到的，按上表状态继续。",
              "老师对上表中的票给出新判断（例如“买点还没出来”“半废”“不行了”）时，要写进新策略包。"]
    return "\n".join(lines) + "\n"


def check_report(pack: Mapping[str, Any], *, instruments: Mapping[str, str], pool: list[Mapping[str, Any]],
                 dry_run: Mapping[str, Any] | None, duplicate: bool) -> dict[str, Any]:
    """Blocking problems and advisory warnings for one pack before import."""
    problems = list(validate_pack(pack))
    warnings: list[str] = []
    if pack.get("pack_id") and pack_digest(pack) != pack.get("pack_id"):
        warnings.append(f"pack_id {pack.get('pack_id')} is not the content digest {pack_digest(pack)}")
    stocks = [stock for stock in pack.get("stocks") or [] if isinstance(stock, Mapping)]
    for stock in stocks:
        symbol = ts_code(str(stock.get("code") or ""))
        if symbol not in instruments:
            problems.append(f"{stock.get('code')} {stock.get('name') or ''}: not in quant.instruments")
        elif stock.get("name") and str(stock["name"]).replace(" ", "") != instruments[symbol].replace(" ", ""):
            warnings.append(f"{stock.get('code')}: pack name {stock['name']} vs instruments {instruments[symbol]}")
    if duplicate:
        problems.append(f"pack {pack.get('pack_id')} is already imported")
    mentioned = {ts_code(str(stock.get("code") or "")): stock for stock in stocks}
    overrides, carried_unmentioned = [], []
    for item in pool:
        if pack.get("pack_id") and item.get("pack_id") == pack.get("pack_id"):
            continue  # this pack's own plans, already applied
        stock = mentioned.get(item["symbol"])
        if stock is None:
            if item.get("review_date") != pack.get("review_date"):
                carried_unmentioned.append({"symbol": item["symbol"], "name": item.get("name"), "state": item.get("state")})
            continue
        effect = "retire" if playbook_kind(str(stock.get("playbook"))) == "record" else "replace"
        overrides.append({"symbol": item["symbol"], "name": item.get("name"), "from_state": item.get("state"),
                          "to_playbook": stock.get("playbook"), "effect": effect})
    dry = dict(dry_run or {})
    if dry.get("status") not in (None, "dry_run"):
        problems.append(f"dry run: {dry.get('status')} {dry.get('problems') or dry.get('reason') or ''}")
    return {
        "ok": not problems, "pack_id": pack.get("pack_id"), "review_date": pack.get("review_date"),
        "problems": problems, "warnings": warnings,
        "counts": {"stocks": len(stocks), "watched": sum(playbook_kind(str(s.get("playbook"))) != "record" for s in stocks),
                   "record_only": sum(playbook_kind(str(s.get("playbook"))) == "record" for s in stocks),
                   "forecasts": len(pack.get("forecasts") or [])},
        "dry_run": {key: dry.get(key) for key in ("session_date", "session_index", "planned", "plan_failures") if key in dry},
        "overrides": overrides, "carried_unmentioned": carried_unmentioned,
    }


# ---------------------------------------------------------------------------
# I/O wrappers (run inside the peer container)
# ---------------------------------------------------------------------------

def _receipts(connection: Any, trade_date: date) -> dict[str, str]:
    from .post_close_refresh import POST_CLOSE_RECEIPT_VERSION
    keys = [f"{POST_CLOSE_RECEIPT_VERSION}:{name}:{trade_date}" for name in PEER_CLOSE_STAGES]
    rows = connection.execute("SELECT run_key, status FROM quant.automation_runs WHERE run_key = ANY(%s)", (keys,)).fetchall()
    found = {str(row["run_key"]).split(":")[1]: str(row["status"]) for row in rows}
    return {name: found.get(name, "missing") for name in PEER_CLOSE_STAGES}


def load_context(database: Any, trade_date: date) -> dict[str, Any]:
    from . import teacher_review_repository as repo
    settlement = next((dict(item.get("payload") or item) for item in repo.recent_settlements(database, limit=10)
                       if str((item.get("payload") or item).get("trade_date")) == trade_date.isoformat()), None)
    rows = repo.teacher_watch_rows(database)
    records = repo.recent_packs(database, since=trade_date - timedelta(days=14))
    superseded = {str(item) for record in records for item in (record["pack"].get("supersedes") or [])}
    packs = [{"pack_id": record["pack"]["pack_id"], "review_date": record["pack"]["review_date"],
              "available_at": str(record["available_at"]), "stocks": len(record["pack"]["stocks"])}
             for record in records if str(record["pack"]["pack_id"]) not in superseded]
    symbols = [item["symbol"] for item in pool_view(rows)]
    bars = repo.session_bars(database, symbols, trade_date) if symbols else {}
    outcome = next((dict(item.get("payload") or {}) for item in repo.recent_outcome_reviews(database, limit=4)
                    if str((item.get("payload") or {}).get("trade_date")) == trade_date.isoformat()), None)
    with database.transaction() as connection:
        receipts = _receipts(connection, trade_date)
    return build_context(trade_date, settlement=settlement, rows=rows, packs=packs, bars=bars,
                         receipts=receipts, outcome=outcome)


async def _dry_run(pack: dict[str, Any]) -> dict[str, Any]:
    """The service's own import in dry-run mode, without provider calls."""
    from dataclasses import replace
    from . import main
    from .teacher_review_service import import_pack

    async def run_database(action: Any, timeout_seconds: float = 30) -> Any:
        return await asyncio.wait_for(asyncio.to_thread(action), timeout=timeout_seconds)

    async def no_alert(_text: str) -> dict[str, Any]:
        return {"status": "dry_run"}

    deps = replace(main._teacher_review_dependencies(), run_database=run_database, send_alert=no_alert,
                   period_bars=None, repair_daily=None)
    result = await import_pack(pack, deps, dry_run=True)
    result.pop("plans", None)
    return result


async def _outcome_run(trade_date: date) -> None:
    """Re-run one session's outcome review in process, without alerting again."""
    from dataclasses import replace
    from . import main
    from .teacher_outcome_review import run as run_outcome_review

    async def run_database(action: Any, timeout_seconds: float = 30) -> Any:
        return await asyncio.wait_for(asyncio.to_thread(action), timeout=timeout_seconds)

    async def no_alert(_text: str) -> dict[str, Any]:
        return {"status": "skipped"}

    deps = replace(main._teacher_review_dependencies(), run_database=run_database, send_alert=no_alert,
                   period_bars=None, repair_daily=None)
    await run_outcome_review(trade_date, deps, alert=False)


def outcome(database: Any, trade_date: date, *, rerun: bool = False) -> dict[str, Any]:
    """The archived next-day outcome review, re-running it first when asked."""
    from . import teacher_review_repository as repo
    from .teacher_outcome_review import outcome_markdown

    def archived() -> dict[str, Any] | None:
        for item in repo.recent_outcome_reviews(database, limit=20):
            payload = dict(item.get("payload") or {})
            if str(payload.get("trade_date")) == trade_date.isoformat():
                return payload
        return None

    report = archived()
    if report is None or rerun:
        asyncio.run(_outcome_run(trade_date))
        report = archived()
    if report is None:
        return {"status": "missing", "trade_date": trade_date.isoformat(),
                "reason": "no settlement for this session yet; run the roll first"}
    return {"status": "ok", "report": report, "markdown": outcome_markdown(trade_date, report)}


def sweep(database: Any, trade_date: date, *, limit: int = 40) -> dict[str, Any]:
    """Replay the session at other thresholds and report both sides of each.

    Post-close only: it reads one row per two minutes per name and evaluates
    every variant over them, which is far too much work to do while the scan
    is running.
    """
    from . import teacher_review_repository as repo
    from .strategy_parameter_sweep import summarize, sweep_lines, sweep_symbol

    outcome = next((dict(item.get("payload") or {}) for item in repo.recent_outcome_reviews(database, limit=4)
                    if str((item.get("payload") or {}).get("trade_date")) == trade_date.isoformat()), None)
    if outcome is None:
        return {"status": "missing", "reason": "run the outcome review for this session first"}
    # Only names the live thresholds did not take: a variant can only change
    # what was blocked, and including the taken ones would double-count them.
    candidates = [item for item in (outcome.get("stocks") or [])
                  if item.get("kind") != "record" and not item.get("entry")][:limit]
    codes = [str(item["code"]) for item in candidates]
    rows = repo.rule_input_snapshots(database, codes, trade_date) if codes else {}
    benchmark = outcome.get("benchmark_session_pct")
    per_symbol = {}
    for item in candidates:
        code = str(item["code"])
        if not rows.get(code):
            continue
        per_symbol[code] = sweep_symbol(ts_code(code), str(item.get("name") or ""), rows[code],
                                        bar=item.get("bar"), benchmark_pct=benchmark)
    table = summarize(per_symbol)
    return {"status": "ok", "trade_date": trade_date.isoformat(), "symbols": len(per_symbol),
            "benchmark_session_pct": benchmark, "variants": table, "lines": sweep_lines(table),
            "research_only": True, "live_effect": "none"}


def digest(database: Any, trade_date: date) -> dict[str, Any]:
    """The session's cross-strategy digest, as a page (no alert is sent)."""
    from dataclasses import replace
    from . import main
    from .daily_research_digest import digest_markdown, run as run_digest

    async def run_database(action: Any, timeout_seconds: float = 30) -> Any:
        return await asyncio.wait_for(asyncio.to_thread(action), timeout=timeout_seconds)

    async def no_alert(_text: str) -> dict[str, Any]:
        return {"status": "skipped"}

    deps = replace(main._teacher_review_dependencies(), run_database=run_database, send_alert=no_alert,
                   period_bars=None, repair_daily=None)
    result = asyncio.run(run_digest(trade_date, deps, alert=False))
    return {"status": result.get("status"), "digest": result.get("digest"),
            "markdown": digest_markdown(trade_date, result.get("digest") or {})}


def check(database: Any, pack: dict[str, Any]) -> dict[str, Any]:
    from . import teacher_review_repository as repo
    codes = [ts_code(str(stock.get("code") or "")) for stock in pack.get("stocks") or [] if isinstance(stock, Mapping)]
    with database.transaction() as connection:
        rows = connection.execute("SELECT symbol, name FROM quant.instruments WHERE symbol = ANY(%s)", (codes,)).fetchall()
    instruments = {str(row["symbol"]): str(row["name"] or "") for row in rows}
    duplicate = bool(pack.get("pack_id")) and repo.pack_record(database, str(pack["pack_id"])) is not None
    structural = validate_pack(pack)
    dry = None if structural or duplicate else asyncio.run(_dry_run(pack))
    return check_report(pack, instruments=instruments, pool=pool_view(repo.teacher_watch_rows(database)),
                        dry_run=dry, duplicate=duplicate)


def import_via_api(database: Any, pack: dict[str, Any], *, timeout_seconds: float = 1500,
                   poll_seconds: float = 600) -> dict[str, Any]:
    """POST the pack to this container's own API; if the reply outlives the socket, poll the archive."""
    from . import teacher_review_repository as repo
    request = urllib.request.Request(
        IMPORT_URL, method="POST",
        data=json.dumps({"pack": pack, "dry_run": False}, ensure_ascii=False).encode(),
        headers={"Content-Type": "application/json", "X-Quant-Write-Key": os.environ.get("QUANT_WRITE_API_KEY", "")},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            body = json.loads(response.read())
        body.pop("plans", None)
        return {"transport": "http", **body}
    except urllib.error.HTTPError as error:
        return {"transport": "http", "status": "http_error", "code": error.code, "detail": error.read().decode()[:500]}
    except (TimeoutError, urllib.error.URLError) as error:
        deadline = time.monotonic() + poll_seconds
        while time.monotonic() < deadline:
            record = repo.pack_record(database, str(pack.get("pack_id")))
            if record is not None:
                return {"transport": "archive_poll", "status": "imported",
                        **((record.get("payload") or {}).get("import") or {})}
            time.sleep(15)
        return {"transport": "http", "status": "unconfirmed", "error": str(error)[:200]}


def main() -> None:  # pragma: no cover - operational entry point
    from .main import db

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    context_parser = sub.add_parser("context")
    context_parser.add_argument("--date", type=date.fromisoformat)
    for name in ("check", "import"):
        sub.add_parser(name).add_argument("--pack", required=True, help="path, or - for stdin")
    sub.add_parser("status")
    outcome_parser = sub.add_parser("outcome")
    outcome_parser.add_argument("--date", type=date.fromisoformat)
    outcome_parser.add_argument("--rerun", action="store_true")
    sweep_parser = sub.add_parser("sweep")
    sweep_parser.add_argument("--date", type=date.fromisoformat)
    digest_parser = sub.add_parser("digest")
    digest_parser.add_argument("--date", type=date.fromisoformat)
    args = parser.parse_args()

    def read_pack() -> dict[str, Any]:
        return json.load(sys.stdin if args.pack == "-" else open(args.pack, encoding="utf-8"))

    if args.command == "context":
        trade_date = args.date or datetime.now(timezone.utc).astimezone(_CN).date()
        context = load_context(db, trade_date)
        output: Any = {"context": context, "markdown": context_markdown(context)}
    elif args.command == "check":
        output = check(db, read_pack())
    elif args.command == "outcome":
        trade_date = args.date or datetime.now(timezone.utc).astimezone(_CN).date()
        output = outcome(db, trade_date, rerun=bool(args.rerun))
    elif args.command == "digest":
        trade_date = args.date or datetime.now(timezone.utc).astimezone(_CN).date()
        output = digest(db, trade_date)
    elif args.command == "sweep":
        trade_date = args.date or datetime.now(timezone.utc).astimezone(_CN).date()
        output = sweep(db, trade_date)
    elif args.command == "import":
        pack = read_pack()
        report = check(db, pack)
        output = {"check": report, "import": import_via_api(db, pack) if report["ok"] else {"status": "not_attempted"}}
    else:
        from . import teacher_review_repository as repo
        output = {"pool": pool_view(repo.teacher_watch_rows(db))}
    print(json.dumps(output, ensure_ascii=False, default=str))


if __name__ == "__main__":  # pragma: no cover
    main()


__all__ = ["build_context", "check_report", "context_markdown", "digest", "outcome", "outcome_digest",
           "pack_digest", "pool_view", "sweep"]
