"""Once-per-session archive of the public post-close evidence.

Several free sources keep only a short window (Eastmoney pools, the THS hot
list) or publish after the close (dragon-tiger list, block trades, holder
changes, margin balances).  Each job below runs once per trading day inside
its window, retries every ten minutes until it succeeds or its window closes,
and writes idempotently, so a restart repeats requests but never duplicates
evidence.
"""

from __future__ import annotations

import asyncio
import time as time_module
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from ..sources import eastmoney_datacenter, eastmoney_ztb, fuyao_evidence
from ..sources.fuyao_evidence import fetch_code_batches
from .intraday import CollectorDeps, CollectorState, SENTIMENT_PROVIDER_KEY, build_sentiment
from ..error_text import error_text
from ..sources.ticks import (
    capital_change_observations, fetch_tdx_capital_changes, fetch_tdx_ticks, fetch_tencent_ticks,
    tick_flow_observation,
)


CN_TZ = ZoneInfo("Asia/Shanghai")
RETRY_SECONDS = 600
DATACENTER_EVENT_REPORTS = (
    "restricted_release", "holder_count", "holder_trade", "block_trade", "earnings_forecast",
    "earnings_express", "repurchase", "ipo_calendar",
)


@dataclass(frozen=True)
class ArchiveJob:
    key: str
    start: time
    end: time
    description: str


JOBS: tuple[ArchiveJob, ...] = (
    ArchiveJob("eastmoney_pools", time(15, 10), time(23, 30), "东财六池收盘归档"),
    ArchiveJob("eastmoney_change_summary", time(15, 10), time(23, 30), "盘口异动全天汇总"),
    ArchiveJob("sentiment_close", time(15, 10), time(17, 0), "收盘情绪指标"),
    ArchiveJob("fuyao_attention_close", time(15, 20), time(23, 30), "同花顺热榜历史与异动终稿"),
    ArchiveJob("fuyao_dragon_tiger", time(17, 30), time(23, 30), "同花顺龙虎榜"),
    ArchiveJob("eastmoney_datacenter", time(18, 0), time(23, 30), "东财解禁/股东/大宗/业绩/回购/新股"),
    ArchiveJob("eastmoney_margin", time(18, 10), time(23, 30), "两融汇总与明细（前一交易日）"),
    ArchiveJob("fuyao_valuation_index", time(18, 30), time(23, 30), "全 A 估值与同花顺指数收盘"),
    ArchiveJob("tick_flow", time(19, 0), time(23, 30), "观察池分笔资金流"),
    ArchiveJob("capital_changes", time(19, 30), time(23, 30), "观察池除权除息与股本变迁"),
)


@dataclass
class ArchiveDeps:
    collector: CollectorDeps
    watch_symbols: Callable[[], Awaitable[Sequence[str]]]
    previous_trading_day: Callable[[date], Awaitable[date | None]]
    margin_detail_enabled: bool = False
    max_tick_symbols: int = 60


@dataclass
class ArchiveState:
    done: dict[str, str] = field(default_factory=dict)
    last_attempt: dict[str, float] = field(default_factory=dict)
    collector_state: CollectorState = field(default_factory=CollectorState)


def _close_of(day: date) -> datetime:
    return datetime.combine(day, time(15, 0), CN_TZ)


def _pool_observations(rows: list[dict[str, Any]], day: date, now: datetime) -> list[dict[str, Any]]:
    effective = min(_close_of(day), now).isoformat()
    return [{**row, "ts_code": row["symbol"], "trade_date": day.isoformat(),
             "effective_at": effective, "available_at": now.isoformat()} for row in rows]


async def job_eastmoney_pools(deps: ArchiveDeps, state: ArchiveState, day: date, now: datetime) -> dict[str, Any]:
    collector = deps.collector
    summary: dict[str, Any] = {}
    for pool in eastmoney_ztb.POOLS:
        rows = await eastmoney_ztb.fetch_pool(pool, day)
        stored = await collector.persist_observations(
            eastmoney_ztb.PROVIDER_KEY, f"limit_pool_{pool}", _pool_observations(rows, day, now)) if rows else 0
        events = eastmoney_ztb.pool_events(rows, now)
        if events:
            await collector.persist_events(eastmoney_ztb.PROVIDER_KEY, events)
        summary[pool] = {"rows": len(rows), "stored": stored, "events": len(events)}
    if not any(item["rows"] for key, item in summary.items() if key in {"limit_up", "previous_limit_up"}):
        raise RuntimeError("Eastmoney pools are still empty for this session")
    return summary


async def job_eastmoney_change_summary(deps: ArchiveDeps, state: ArchiveState, day: date, now: datetime) -> dict[str, Any]:
    rows = await eastmoney_ztb.fetch_stock_changes(tuple(eastmoney_ztb.STOCK_CHANGE_TYPES))
    if not rows:
        raise RuntimeError("Eastmoney anomaly tape is empty")
    summary = eastmoney_ztb.stock_change_summary(rows, day)
    stored = await deps.collector.persist_observations(eastmoney_ztb.PROVIDER_KEY, "stock_change_daily_summary", [{
        **summary, "ts_code": None, "effective_at": min(_close_of(day), now).isoformat(), "available_at": now.isoformat(),
    }])
    return {"rows": len(rows), "symbols": len(summary["by_symbol"]), "stored": stored}


async def job_sentiment_close(deps: ArchiveDeps, state: ArchiveState, day: date, now: datetime) -> dict[str, Any]:
    reading = await build_sentiment(deps.collector, state.collector_state, now)
    stored = await deps.collector.persist_observations(SENTIMENT_PROVIDER_KEY, "market_sentiment_close", [{
        **reading, "ts_code": None, "effective_at": min(_close_of(day), now).isoformat(), "available_at": now.isoformat(),
    }])
    return {"stored": stored, "limit_up_count": reading["limit_up_count"], "seal_rate": reading["seal_rate"]}


def _fetch(deps: ArchiveDeps) -> Callable[[str, dict[str, Any]], Awaitable[Mapping[str, Any]]]:
    if deps.collector.fuyao_fetch is None:
        raise RuntimeError("Fuyao is not configured")
    return deps.collector.fuyao_fetch


async def job_fuyao_attention_close(deps: ArchiveDeps, state: ArchiveState, day: date, now: datetime) -> dict[str, Any]:
    fetch = _fetch(deps)
    history = await fetch("a_share_hot_stock_list_history", {"date": day.isoformat()})
    rows = fuyao_evidence.hot_history_observations(history, now)
    if not rows:
        raise RuntimeError("THS hot list history for this session is not published yet")
    stored = await deps.collector.persist_observations("fuyao_ths", "a_share_hot_stock_list_history", rows)
    anomalies = fuyao_evidence.anomaly_events(await fetch("a_share_anomaly_analysis_list", {}), now)
    events = await deps.collector.persist_events("fuyao_ths", anomalies) if anomalies else 0
    return {"hot_history": len(rows), "stored": stored, "anomalies": events}


async def job_fuyao_dragon_tiger(deps: ArchiveDeps, state: ArchiveState, day: date, now: datetime) -> dict[str, Any]:
    """Archive the latest published list; done only once it is this session's.

    The list can publish late in the evening or the next morning.  Whatever
    date comes back is stored (identity per stock and trade date), so a list
    first seen the next session is still archived rather than skipped.
    """
    data = await _fetch(deps)("a_share_dragon_tiger_list", {"board_type": "all"})
    trade_date = str(data.get("trade_date") or "")[:10]
    events = fuyao_evidence.dragon_tiger_events(data, now)
    stored = await deps.collector.persist_events("fuyao_ths", events) if events else 0
    hot_money = fuyao_evidence.dragon_tiger_hot_money_observations(data, now)
    if hot_money:
        await deps.collector.persist_observations("fuyao_ths", "lhb_hot_money", hot_money)
    if trade_date != day.isoformat():
        raise RuntimeError(f"dragon-tiger list still dated {trade_date or 'unknown'} (stored {stored} rows of it)")
    return {"trade_date": trade_date, "stocks": len(events), "stored": stored, "hot_money": len(hot_money)}


async def job_eastmoney_datacenter(deps: ArchiveDeps, state: ArchiveState, day: date, now: datetime) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    failures = 0
    for key in DATACENTER_EVENT_REPORTS:
        start, end = eastmoney_datacenter.default_window(key, day)
        try:
            rows = await eastmoney_datacenter.fetch_report(key, start=start, end=end)
            events = eastmoney_datacenter.report_events(key, rows, now)
            stored = await deps.collector.persist_events(eastmoney_datacenter.PROVIDER_KEY, events) if events else 0
            summary[key] = {"rows": len(rows), "stored": stored}
        except Exception as error:  # noqa: BLE001 - one report must not block the others
            failures += 1
            summary[key] = {"status": "failed", "error": error_text(error, 160)}
        await asyncio.sleep(0.3)
    if failures == len(DATACENTER_EVENT_REPORTS):
        raise RuntimeError(f"every datacenter report failed: {summary}")
    return summary


async def job_eastmoney_margin(deps: ArchiveDeps, state: ArchiveState, day: date, now: datetime) -> dict[str, Any]:
    previous = await deps.previous_trading_day(day)
    if previous is None:
        raise RuntimeError("no previous trading day in the calendar")
    market = await eastmoney_datacenter.fetch_report("margin_market", start=previous - timedelta(days=5), end=previous)
    stored = await deps.collector.persist_observations(
        eastmoney_datacenter.PROVIDER_KEY, "margin_market", eastmoney_datacenter.report_observations("margin_market", market, now))
    result: dict[str, Any] = {"margin_market_rows": len(market), "stored": stored}
    if deps.margin_detail_enabled:
        detail = await eastmoney_datacenter.fetch_report("margin_detail", start=previous, end=previous, max_pages=20)
        result["margin_detail_rows"] = len(detail)
        result["margin_detail_stored"] = await deps.collector.persist_observations(
            eastmoney_datacenter.PROVIDER_KEY, "margin_detail", eastmoney_datacenter.report_observations("margin_detail", detail, now))
    return result


async def job_fuyao_valuation_index(deps: ArchiveDeps, state: ArchiveState, day: date, now: datetime) -> dict[str, Any]:
    fetch = _fetch(deps)
    if deps.collector.fuyao_snapshot is None:
        raise RuntimeError("Fuyao snapshot is not configured")
    snapshot_rows, _meta = await deps.collector.fuyao_snapshot()
    codes = [row["symbol"] for row in snapshot_rows]
    batches, dropped, failures = await fetch_code_batches(fetch, "a_share_valuations_snapshot", codes)
    valuations = [row for _codes, data in batches for row in fuyao_evidence.valuation_observations(data, now)]
    stored = await deps.collector.persist_observations("fuyao_ths", "a_share_valuations_snapshot", valuations) if valuations else 0
    index_rows: list[dict[str, Any]] = []
    for tag in ("cn_concept", "industry", "region", "tszs"):
        names = fuyao_evidence.index_catalog(await fetch("ths_index_list", {"tag": tag}))
        index_codes = sorted(names)
        for offset in range(0, len(index_codes), 100):
            data = await fetch("ths_index_prices_snapshot", {"thscodes": ",".join(index_codes[offset:offset + 100])})
            index_rows.extend({**row, "index_tag": tag} for row in fuyao_evidence.index_quote_observations(data, now, names))
    index_stored = await deps.collector.persist_observations("fuyao_ths", "ths_index_prices_snapshot", index_rows) if index_rows else 0
    return {"valuations": len(valuations), "stored": stored, "dropped_codes": dropped[:20], "failures": failures[:5],
            "index_quotes": len(index_rows), "index_stored": index_stored}


async def job_tick_flow(deps: ArchiveDeps, state: ArchiveState, day: date, now: datetime) -> dict[str, Any]:
    symbols = list(await deps.watch_symbols())[:deps.max_tick_symbols]
    observations, sources, failures = [], {"tdx_public": 0, "tencent_free": 0}, []
    for symbol in symbols:
        try:
            ticks, _host = await fetch_tdx_ticks(symbol, day)
            source = "tdx_public"
        except Exception as tdx_error:  # noqa: BLE001 - Tencent covers today's prints
            try:
                ticks, source = await fetch_tencent_ticks(symbol), "tencent_free"
            except Exception as tencent_error:  # noqa: BLE001
                failures.append(f"{symbol}:{type(tdx_error).__name__}/{type(tencent_error).__name__}")
                continue
        if ticks:
            observations.append(tick_flow_observation(symbol, day, ticks, source=source, observed_at=now))
            sources[source] += 1
    stored = await deps.collector.persist_observations("derived_tick_flow", "tick_flow_daily", observations) if observations else 0
    if symbols and not observations:
        raise RuntimeError(f"no tick source answered: {failures[:5]}")
    return {"symbols": len(symbols), "stored": stored, "sources": sources, "failures": failures[:10]}


async def job_capital_changes(deps: ArchiveDeps, state: ArchiveState, day: date, now: datetime) -> dict[str, Any]:
    symbols = list(await deps.watch_symbols())[:deps.max_tick_symbols]
    rows, failures = [], []
    for symbol in symbols:
        try:
            changes, _host = await fetch_tdx_capital_changes(symbol)
            rows.extend(capital_change_observations(symbol, changes, now))
        except Exception as error:  # noqa: BLE001
            failures.append(f"{symbol}:{type(error).__name__}")
    stored = await deps.collector.persist_observations("tdx_public", "capital_changes", rows) if rows else 0
    if symbols and not rows:
        raise RuntimeError(f"no TDX host answered the capital log: {failures[:5]}")
    return {"symbols": len(symbols), "rows": len(rows), "stored": stored, "failures": failures[:10]}


RUNNERS: dict[str, Callable[[ArchiveDeps, ArchiveState, date, datetime], Awaitable[dict[str, Any]]]] = {
    "eastmoney_pools": job_eastmoney_pools, "eastmoney_change_summary": job_eastmoney_change_summary,
    "sentiment_close": job_sentiment_close, "fuyao_attention_close": job_fuyao_attention_close,
    "fuyao_dragon_tiger": job_fuyao_dragon_tiger, "eastmoney_datacenter": job_eastmoney_datacenter,
    "eastmoney_margin": job_eastmoney_margin, "fuyao_valuation_index": job_fuyao_valuation_index,
    "tick_flow": job_tick_flow, "capital_changes": job_capital_changes,
}


def due_jobs(state: ArchiveState, now: datetime, *, trading_day: bool, monotonic: float,
             enabled: Mapping[str, bool] | None = None) -> list[ArchiveJob]:
    if not trading_day:
        return []
    local = now.astimezone(CN_TZ)
    today = local.date().isoformat()
    jobs = []
    for job in JOBS:
        if enabled is not None and not enabled.get(job.key, True):
            continue
        if state.done.get(job.key) == today or not job.start <= local.time() <= job.end:
            continue
        last = state.last_attempt.get(job.key)
        if last is not None and monotonic - last < RETRY_SECONDS:
            continue
        jobs.append(job)
    return jobs


async def run_due_jobs(deps: ArchiveDeps, state: ArchiveState, now: datetime, *, trading_day: bool,
                       enabled: Mapping[str, bool] | None = None) -> dict[str, Any]:
    results: dict[str, Any] = {}
    day = now.astimezone(CN_TZ).date()
    for job in due_jobs(state, now, trading_day=trading_day, monotonic=time_module.monotonic(), enabled=enabled):
        state.last_attempt[job.key] = time_module.monotonic()
        try:
            results[job.key] = {"status": "completed", **await RUNNERS[job.key](deps, state, day, now)}
            state.done[job.key] = day.isoformat()
            await deps.collector.record_health("public_archive", job.key, True, 1, None, None)
        except Exception as error:  # noqa: BLE001 - retried on the next window tick
            results[job.key] = {"status": "failed", "error": error_text(error, 240)}
            try:
                await deps.collector.record_health("public_archive", job.key, False, 0, None, error_text(error, 500))
            except Exception:  # noqa: BLE001
                pass
    return results


async def run_loop(
    deps: ArchiveDeps,
    *,
    trading_day: Callable[[date], Awaitable[bool]],
    enabled: Mapping[str, bool] | None = None,
    tick_seconds: int = 60,
) -> None:
    state = ArchiveState()
    while True:
        now = datetime.now(timezone.utc)
        try:
            open_day = await trading_day(now.astimezone(CN_TZ).date())
        except Exception:  # noqa: BLE001 - an unknown calendar skips, never guesses
            open_day = False
        results = await run_due_jobs(deps, state, now, trading_day=open_day, enabled=enabled)
        failed = {key: value for key, value in results.items() if value.get("status") == "failed"}
        if failed:
            deps.collector.log(f"post-close public archive retrying: {str(failed)[:500]}")
        await asyncio.sleep(max(15, tick_seconds))


__all__ = [
    "ArchiveDeps", "ArchiveJob", "ArchiveState", "DATACENTER_EVENT_REPORTS", "JOBS", "RUNNERS",
    "due_jobs", "run_due_jobs", "run_loop",
]
