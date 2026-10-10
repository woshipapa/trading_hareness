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

from ..derived import limit_pools
from ..http import ashare_symbol
from ..sources import eastmoney_datacenter, eastmoney_ztb, fuyao_evidence
from ..sources import (
    tdx_bars, tdx_files, tdx_fin_history, tdx_instruments, tdx_mac, tdx_protocol, tdx_reference_files,
    tdx_zhb_extras,
)
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
    ArchiveJob("daily_valuation_projection", time(18, 30), time(23, 30), "复用已归档估值补齐每日记录"),
    ArchiveJob("tick_flow", time(19, 0), time(23, 30), "观察池分笔资金流"),
    ArchiveJob("capital_changes", time(19, 30), time(23, 30), "观察池除权除息与股本变迁"),
    ArchiveJob("tdx_security_list", time(19, 40), time(23, 30), "通达信证券列表变更"),
    ArchiveJob("tdx_tipinfo", time(19, 50), time(23, 30), "通达信财报首次披露日期"),
    ArchiveJob("tdx_gpcw", time(20, 0), time(23, 30), "通达信历史财务报表"),
    ArchiveJob("tdx_index_bars", time(20, 10), time(23, 30), "通达信指数日线与涨跌家数"),
    ArchiveJob("tdx_mac_boards", time(20, 20), time(23, 30), "通达信板块目录与成分"),
    ArchiveJob("tdx_limit_pools", time(20, 30), time(23, 30), "通达信衍生涨跌停池"),
    ArchiveJob("tdx_host_probe", time(20, 40), time(23, 30), "通达信主机周探针"),
    ArchiveJob("tdx_stat_snapshot", time(20, 50), time(23, 30), "通达信估值与每日统计快照"),
    ArchiveJob("tdx_files_membership", time(21, 0), time(23, 30), "通达信文件板块成分"),
    ArchiveJob("tdx_calendar_ipo", time(21, 10), time(23, 30), "通达信节假日与新股日历"),
)


@dataclass
class ArchiveDeps:
    collector: CollectorDeps
    watch_symbols: Callable[[], Awaitable[Sequence[str]]]
    previous_trading_day: Callable[[date], Awaitable[date | None]]
    margin_detail_enabled: bool = False
    max_tick_symbols: int = 60
    project_valuations: Callable[[date], Awaitable[Mapping[str, Any]]] | None = None
    latest_observation_payloads: Callable[[str, str], Awaitable[dict[str, dict[str, Any]]]] | None = None
    observation_payloads: Callable[[str, str], Awaitable[list[dict[str, Any]]]] | None = None
    max_gpcw_periods: int = 2
    persist_membership_delta: Callable[[str, str, dict[str, dict[str, Any]], datetime], Awaitable[dict[str, int]]] | None = None


TDX_INDEX_SYMBOLS = ("999999.SH", "399001.SZ", "399006.SZ", "399300.SZ", "000688.SH", "899050.BJ")


@dataclass
class ArchiveState:
    done: dict[str, str] = field(default_factory=dict)
    weekly_done: dict[str, str] = field(default_factory=dict)
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


class NotYetPublished(RuntimeError):
    """The source has not published this session's data yet.

    The job is retried on the next tick inside its window, but this is not a provider
    failure: counting it as one opened the public_archive circuit every evening (the
    dragon-tiger job had 274 consecutive "failures" by 2026-10-09).
    """


def _fetch(deps: ArchiveDeps) -> Callable[[str, dict[str, Any]], Awaitable[Mapping[str, Any]]]:
    if deps.collector.fuyao_fetch is None:
        raise RuntimeError("Fuyao is not configured")
    return deps.collector.fuyao_fetch


async def job_fuyao_attention_close(deps: ArchiveDeps, state: ArchiveState, day: date, now: datetime) -> dict[str, Any]:
    fetch = _fetch(deps)
    history = await fetch("a_share_hot_stock_list_history", {"date": day.isoformat()})
    rows = fuyao_evidence.hot_history_observations(history, now)
    if not rows:
        raise NotYetPublished("THS hot list history for this session is not published yet")
    stored = await deps.collector.persist_observations("fuyao_ths", "a_share_hot_stock_list_history", rows)
    anomalies = fuyao_evidence.anomaly_events(await fetch("a_share_anomaly_analysis_list", {}), now)
    events = await deps.collector.persist_events("fuyao_ths", anomalies) if anomalies else 0
    return {"hot_history": len(rows), "stored": stored, "anomalies": events}


async def job_fuyao_dragon_tiger(deps: ArchiveDeps, state: ArchiveState, day: date, now: datetime) -> dict[str, Any]:
    """Archive this session's list, asked for by date.

    Without a date the route answers with its latest list, which lags: at 19:11 on
    2026-10-09 it still returned the 10-08 list while a request dated 10-09 already
    returned 76 rows, so the job never completed (274 consecutive failures). Whatever
    date comes back is still stored (identity per stock and trade date); the job is
    done only once this session's list has arrived, and until then it is pending.
    """
    data = await _fetch(deps)("a_share_dragon_tiger_list", {"board_type": "all", "date": day.isoformat()})
    trade_date = str(data.get("trade_date") or "")[:10]
    events = fuyao_evidence.dragon_tiger_events(data, now)
    stored = await deps.collector.persist_events("fuyao_ths", events) if events else 0
    hot_money = fuyao_evidence.dragon_tiger_hot_money_observations(data, now)
    if hot_money:
        await deps.collector.persist_observations("fuyao_ths", "lhb_hot_money", hot_money)
    if trade_date != day.isoformat() or not events:
        raise NotYetPublished(f"dragon-tiger list for {day} not published yet "
                              f"(got {trade_date or 'no date'}, stored {stored} rows of it)")
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
    dropped_indices: list[str] = []
    for tag in ("cn_concept", "industry", "region", "tszs"):
        names = fuyao_evidence.index_catalog(await fetch("ths_index_list", {"tag": tag}))
        # The catalog can list an index the quote route no longer knows ("Unknown thscode:
        # 886113.TI" failed the whole job 38 times by 2026-10-09); drop it and go on.
        index_batches, index_dropped, index_failures = await fetch_code_batches(
            fetch, "ths_index_prices_snapshot", list(names), code_filter=fuyao_evidence.index_codes)
        dropped_indices.extend(index_dropped)
        failures.extend(index_failures)
        for _codes, data in index_batches:
            index_rows.extend({**row, "index_tag": tag} for row in fuyao_evidence.index_quote_observations(data, now, names))
    index_stored = await deps.collector.persist_observations("fuyao_ths", "ths_index_prices_snapshot", index_rows) if index_rows else 0
    return {"valuations": len(valuations), "stored": stored, "dropped_codes": dropped[:20], "failures": failures[:5],
            "index_quotes": len(index_rows), "index_stored": index_stored, "dropped_index_codes": dropped_indices[:20],
            "status": "completed"}


async def job_daily_valuation_projection(deps: ArchiveDeps, state: ArchiveState, day: date, now: datetime) -> dict[str, Any]:
    """Retry persisted-evidence projection independently of provider capture."""
    if deps.project_valuations is None:
        raise RuntimeError('daily valuation projection is disabled')
    projection = dict(await deps.project_valuations(day))
    return {'daily_valuation_projection': projection,
            'status': 'completed' if projection.get('status') in {'completed', 'unchanged'} else 'pending'}


async def job_tick_flow(deps: ArchiveDeps, state: ArchiveState, day: date, now: datetime) -> dict[str, Any]:
    started = time_module.monotonic()
    symbols = list(await deps.watch_symbols())[:deps.max_tick_symbols]
    observations, sources, failures, tdx_failures, tdx_rows = [], {"tdx_public": 0, "tencent_free": 0}, [], [], 0
    for symbol in symbols:
        try:
            ticks, _host = await fetch_tdx_ticks(symbol, day)
            source = "tdx_public"
            tdx_rows += len(ticks)
        except Exception as tdx_error:  # noqa: BLE001 - Tencent covers today's prints
            tdx_failures.append(f"{symbol}:{type(tdx_error).__name__}")
            try:
                ticks, source = await fetch_tencent_ticks(symbol), "tencent_free"
            except Exception as tencent_error:  # noqa: BLE001
                failures.append(f"{symbol}:{type(tdx_error).__name__}/{type(tencent_error).__name__}")
                continue
        if ticks:
            observations.append(tick_flow_observation(symbol, day, ticks, source=source, observed_at=now))
            sources[source] += 1
    stored = await deps.collector.persist_observations("derived_tick_flow", "tick_flow_daily", observations) if observations else 0
    if symbols:
        await deps.collector.record_health("tdx_public", "ticks.session", sources["tdx_public"] > 0, tdx_rows,
                                           round((time_module.monotonic() - started) * 1000),
                                           ",".join(tdx_failures[:5]) or None)
    if symbols and not observations:
        raise RuntimeError(f"no tick source answered: {failures[:5]}")
    return {"symbols": len(symbols), "stored": stored, "sources": sources, "failures": failures[:10]}


async def job_capital_changes(deps: ArchiveDeps, state: ArchiveState, day: date, now: datetime) -> dict[str, Any]:
    started = time_module.monotonic()
    symbols = list(await deps.watch_symbols())[:deps.max_tick_symbols]
    rows, failures = [], []
    for symbol in symbols:
        try:
            changes, _host = await fetch_tdx_capital_changes(symbol)
            rows.extend(capital_change_observations(symbol, changes, now))
        except Exception as error:  # noqa: BLE001
            failures.append(f"{symbol}:{type(error).__name__}")
    stored = await deps.collector.persist_observations("tdx_public", "capital_changes", rows) if rows else 0
    if symbols:
        await deps.collector.record_health("tdx_public", "fundamentals.capital_changes", len(failures) < len(symbols),
                                           len(rows), round((time_module.monotonic() - started) * 1000),
                                           ",".join(failures[:5]) or None)
    if symbols and not rows:
        raise RuntimeError(f"no TDX host answered the capital log: {failures[:5]}")
    return {"symbols": len(symbols), "rows": len(rows), "stored": stored, "failures": failures[:10]}


#: The reference fields of a security-list row; its pre_close moves every day and its answering host every run.
TDX_SECURITY_FIELDS = ("symbol", "market", "code", "name", "instrument_type", "decimal_point", "is_st", "list_source")


async def job_tdx_security_list(deps: ArchiveDeps, state: ArchiveState, day: date, now: datetime) -> dict[str, Any]:
    """Archive the reference fields of the TDX security list: the first run stores every security (about 52,000 rows),
    later runs only the securities whose name, type, decimal point, ST flag or list source changed."""
    started = time_module.monotonic()
    evidence = await tdx_instruments.fetch_security_list()
    rows = evidence.rows
    previous = await deps.latest_observation_payloads("tdx_public", "tdx_security_list") if deps.latest_observation_payloads else {}
    changed = []
    for row in rows:
        reference = {key: row[key] for key in TDX_SECURITY_FIELDS}
        stored = previous.get(row["symbol"], {})
        if {key: stored.get(key) for key in TDX_SECURITY_FIELDS} != reference:
            changed.append({**reference, "ts_code": row["symbol"], "effective_at": now.isoformat(), "available_at": now.isoformat()})
    stored = await deps.collector.persist_observations("tdx_public", "tdx_security_list", changed) if changed else 0
    await deps.collector.record_health("tdx_public", "tdx_security_list", True, len(rows),
                                       round((time_module.monotonic() - started) * 1000), None)
    return {"rows": len(rows), "changed": len(changed), "stored": stored}


async def job_tdx_tipinfo(deps: ArchiveDeps, state: ArchiveState, day: date, now: datetime) -> dict[str, Any]:
    """Archive every tipinfo disclosure row; a normal day stores about 5,600 rows once."""
    started = time_module.monotonic()

    def fetch(client):
        files = tdx_files.parse_zhb_zip(tdx_files.download(client, "zhb.zip"))
        return tdx_zhb_extras.parse_tipinfo(files["tipinfo.dat"])

    rows, host = await tdx_protocol.call(fetch, handshake_profile="login_one")
    observations = []
    for row in rows:
        market = int(row["market"])
        symbol = tdx_protocol.symbol(market, row["code"])
        effective = datetime.combine(row["first_disclosure_date"], time(23, 59, 59), CN_TZ)
        observations.append({"ts_code": symbol, "market": market, "code": row["code"],
                             "report_period": row["report_period"], "eps": row["eps"],
                             "first_disclosure_date": row["first_disclosure_date"],
                             "effective_at": effective.isoformat(), "available_at": now.isoformat()})
    stored = await deps.collector.persist_observations("tdx_public", "tdx_tipinfo", observations)
    await deps.collector.record_health("tdx_public", "tdx_tipinfo", True, len(rows),
                                       round((time_module.monotonic() - started) * 1000), None)
    return {"rows": len(rows), "stored": stored, "host": host}


async def job_tdx_gpcw(deps: ArchiveDeps, state: ArchiveState, day: date, now: datetime) -> dict[str, Any]:
    """On the first enabled run archive the newest two periods; later runs drain the remaining backlog two at a time."""
    started = time_module.monotonic()
    manifest_text, host = await tdx_protocol.call(
        lambda client: tdx_protocol.decode_text(tdx_files.download(client, "tdxfin/gpcw.txt")),
        handshake_profile="login_one",
    )
    manifest = tdx_fin_history.parse_manifest(manifest_text)
    previous_payloads = await deps.latest_observation_payloads("tdx_public", "tdx_gpcw_manifest") if deps.latest_observation_payloads else {}
    previous = [tdx_fin_history.ManifestEntry(item["filename"], item["md5"], int(item["size"]))
                for item in previous_payloads.values()]
    changes = tdx_fin_history.manifest_changes(previous, manifest)
    changed_names = sorted(set(changes["added"]) | set(changes["changed"]), reverse=True)[:deps.max_gpcw_periods]
    entries = {entry.filename: entry for entry in manifest}
    tipinfo_payloads = await deps.observation_payloads("tdx_public", "tdx_tipinfo") if deps.observation_payloads else []
    tipinfo = [{**row, "first_disclosure_date": date.fromisoformat(str(row["first_disclosure_date"]))}
               for row in tipinfo_payloads]
    stored = 0
    downloaded = 0
    undated = 0
    rejected = 0
    manifest_stored = 0
    for filename in changed_names:
        entry = entries[filename]
        rows, _period_host = await tdx_protocol.call(
            lambda client, name=filename, item=entry: tdx_fin_history.gpcw(client, name, item),
            handshake_profile="login_one",
        )
        undated += tdx_fin_history.date_gpcw_rows(rows, tipinfo)
        observations = []
        for row in rows:
            symbol = ashare_symbol(row["code"])
            if symbol is None:
                rejected += 1
                continue
            named_fields = {name: value for name, value in row["fields"].items() if name in tdx_fin_history.GPCW_FIELD_NAMES.values()}
            report_period = row["report_period"]
            effective = datetime.combine(date.fromisoformat(report_period), time(15, 0), CN_TZ)
            available = row.get("available_at", now)
            observations.append({"ts_code": symbol, "code": row["code"], "report_period": report_period,
                                "fields": named_fields, "field_units": {key: row["field_units"][key] for key in named_fields},
                                "effective_at": effective.isoformat(), "available_at": available.isoformat(),
                                "availability_basis": "tipinfo_first_disclosure" if "available_at" in row else "collection_time_undated"})
        period_stored = await deps.collector.persist_observations("tdx_public", "tdx_gpcw", observations) if observations else 0
        stored += period_stored
        if period_stored:
            manifest_stored += await deps.collector.persist_observations(
                "tdx_public", "tdx_gpcw_manifest", [{"observation_symbol": entry.filename, "filename": entry.filename,
                "md5": entry.md5, "size": entry.size,
                "effective_at": datetime.combine(date.fromisoformat(entry.filename[4:12]), time(15, 0), CN_TZ).isoformat(),
                "available_at": now.isoformat()}])
            downloaded += 1
    await deps.collector.record_health("tdx_public", "tdx_gpcw", True, stored, round((time_module.monotonic() - started) * 1000), None)
    return {"manifest": len(manifest), "manifest_stored": manifest_stored, "changed": changed_names,
            "downloaded": downloaded, "rows_stored": stored, "undated": undated, "rejected": rejected, "host": host}


async def job_tdx_index_bars(deps: ArchiveDeps, state: ArchiveState, day: date, now: datetime) -> dict[str, Any]:
    """Archive six index series; each symbol stores 800 rows once and five rows on normal later days."""
    started = time_module.monotonic()
    daily_rows: list[dict[str, Any]] = []
    breadth_rows: list[dict[str, Any]] = []
    previous = await deps.latest_observation_payloads("tdx_public", "tdx_index_daily_bars") if deps.latest_observation_payloads else {}
    for symbol in TDX_INDEX_SYMBOLS:
        count = 5 if symbol in previous else 800
        daily = await tdx_bars.fetch_index_daily(symbol=symbol, count=count)
        for row in daily.rows:
            effective = datetime.combine(date.fromisoformat(row["trade_date"]), time(15, 0), CN_TZ)
            daily_rows.append({**row, "ts_code": symbol, "effective_at": effective.isoformat(), "available_at": now.isoformat()})
            breadth_rows.append({key: row[key] for key in ("symbol", "trade_date", "up_count", "down_count")}
                                | {"ts_code": symbol, "effective_at": effective.isoformat(), "available_at": now.isoformat()})
    stored_daily = await deps.collector.persist_observations("tdx_public", "tdx_index_daily_bars", daily_rows)
    stored_breadth = await deps.collector.persist_observations("tdx_public", "tdx_index_breadth", breadth_rows)
    await deps.collector.record_health("tdx_public", "tdx_index_daily_bars", True, len(daily_rows),
                                       round((time_module.monotonic() - started) * 1000), None)
    return {"symbols": len(TDX_INDEX_SYMBOLS), "daily_rows": len(daily_rows), "breadth_rows": len(breadth_rows),
            "stored_daily": stored_daily, "stored_breadth": stored_breadth}


async def job_tdx_mac_boards(deps: ArchiveDeps, state: ArchiveState, day: date, now: datetime) -> dict[str, Any]:
    """Archive MAC board metadata and observed membership; a full pass is five plus one request per board."""
    started = time_module.monotonic()
    catalog = await tdx_mac.fetch_board_catalog()
    effective = _close_of(day).isoformat()
    catalog_rows = [{**row, "ts_code": f"{row['board_code']}.SH", "effective_at": effective,
                     "available_at": now.isoformat()} for row in catalog.rows]
    stored_catalog = await deps.collector.persist_observations("tdx_mac", "tdx_mac_board_catalog", catalog_rows)
    membership_requests = 0
    opened = 0
    closed = 0
    for board in catalog.rows:
        evidence = await tdx_mac.fetch_membership(sector_key=board["board_code"], board_type=board["board_type"])
        members = {row["symbol"]: row for row in evidence.rows}
        membership_requests += 1
        if deps.persist_membership_delta is not None:
            delta = await deps.persist_membership_delta(
                f"tdx_mac_type_{board['board_type']}", board["board_code"], members, now,
            )
            opened += delta["opened"]
            closed += delta["closed"]
    await deps.collector.record_health("tdx_mac", "sector.membership", True, len(catalog.rows),
                                       round((time_module.monotonic() - started) * 1000), None)
    return {"boards": len(catalog.rows), "catalog_requests": len(tdx_mac.BOARD_TYPES),
            "membership_requests": membership_requests, "stored_catalog": stored_catalog,
            "opened": opened, "closed": closed}


async def job_tdx_limit_pools(deps: ArchiveDeps, state: ArchiveState, day: date, now: datetime) -> dict[str, Any]:
    """Archive the three derived TDX limit pools once after close; a normal day stores one row per pool member."""
    started = time_module.monotonic()
    pools = await limit_pools._read_limit_pools(trade_date=day)
    effective = _close_of(day).isoformat()
    stored: dict[str, int] = {}
    counts: dict[str, int] = {}
    for pool_name, evidence in pools.items():
        rows = [{**row, "ts_code": row["symbol"], "trade_date": day.isoformat(),
                 "effective_at": effective, "available_at": now.isoformat()} for row in evidence.rows]
        counts[pool_name] = len(rows)
        stored[pool_name] = await deps.collector.persist_observations("tdx_public", f"tdx_{pool_name}_pool", rows)
    await deps.collector.record_health("tdx_public", "tdx_limit_pools", True, sum(counts.values()),
                                       round((time_module.monotonic() - started) * 1000), None)
    return {"counts": counts, "stored": stored}


async def job_tdx_host_probe(deps: ArchiveDeps, state: ArchiveState, day: date, now: datetime) -> dict[str, Any]:
    """Probe every configured TDX host weekly; a normal run writes one health row per host."""
    week = f"{day.isocalendar().year}-W{day.isocalendar().week:02d}"
    if state.weekly_done.get("tdx_host_probe") == week:
        return {"status": "skipped", "week": week}
    results = []
    for host in tdx_protocol.configured_hosts():
        started = time_module.monotonic()
        try:
            count, receipt = await asyncio.to_thread(
                tdx_protocol.call_sync,
                lambda client: tdx_instruments.security_count(client, 0), hosts=[host], handshake_profile="login_one",
            )
            latency = round((time_module.monotonic() - started) * 1000)
            await deps.collector.record_health("tdx_public", f"tdx_host:{receipt}", True, int(count), latency, None)
            results.append({"host": receipt, "ok": True, "latency_ms": latency, "count": count})
        except Exception as error:  # noqa: BLE001 - one host failure must not hide other host health
            latency = round((time_module.monotonic() - started) * 1000)
            label = f"{host[0]}:{host[1]}"
            await deps.collector.record_health("tdx_public", f"tdx_host:{label}", False, 0, latency, type(error).__name__)
            results.append({"host": label, "ok": False, "latency_ms": latency, "error": type(error).__name__})
    state.weekly_done["tdx_host_probe"] = week
    return {"status": "completed", "week": week, "hosts": results}


def _tdx_date(value: Any) -> date:
    text = str(value)
    return date.fromisoformat(text if "-" in text else f"{text[:4]}-{text[4:6]}-{text[6:8]}")


async def job_tdx_stat_snapshot(deps: ArchiveDeps, state: ArchiveState, day: date, now: datetime) -> dict[str, Any]:
    """Archive about 8,000 valuation and 8,000 daily-basic rows on a normal full snapshot day."""
    started = time_module.monotonic()
    valuation = await tdx_reference_files.fetch_valuation()
    daily_basic = await tdx_reference_files.fetch_daily_basic()
    valuation_rows = [{**row, "ts_code": row["symbol"],
                       "effective_at": _close_of(_tdx_date(row["effective_date"])).isoformat(),
                       "available_at": now.isoformat()} for row in valuation.rows]
    daily_rows = [{**row, "ts_code": row["symbol"],
                   "effective_at": _close_of(_tdx_date(row["effective_date"])).isoformat(),
                   "available_at": now.isoformat()} for row in daily_basic.rows]
    stored_valuation = await deps.collector.persist_observations("tdx_public", "tdxstat_valuation", valuation_rows)
    stored_daily = await deps.collector.persist_observations("tdx_public", "tdxstat2_daily_basic", daily_rows)
    await deps.collector.record_health("tdx_public", "tdx_stat_snapshot", True, len(valuation_rows) + len(daily_rows),
                                       round((time_module.monotonic() - started) * 1000), None)
    return {"valuation_rows": len(valuation_rows), "daily_basic_rows": len(daily_rows),
            "stored_valuation": stored_valuation, "stored_daily_basic": stored_daily}


async def job_tdx_files_membership(deps: ArchiveDeps, state: ArchiveState, day: date, now: datetime) -> dict[str, Any]:
    """Archive one observed row per returned TDX file member; unchanged members store no new interval."""
    started = time_module.monotonic()
    evidence = await tdx_reference_files.fetch_membership()
    grouped: dict[tuple[str, str], dict[str, dict[str, Any]]] = {}
    for row in evidence.rows:
        grouped.setdefault((row["taxonomy_key"], row["sector_key"]), {})[row["symbol"]] = row
    opened = closed = 0
    for (taxonomy_key, sector_key), members in grouped.items():
        if deps.persist_membership_delta is None:
            continue
        delta = await deps.persist_membership_delta(taxonomy_key, sector_key, members, now)
        opened += delta["opened"]
        closed += delta["closed"]
    await deps.collector.record_health("tdx_public", "tdx_files_membership", True, len(evidence.rows),
                                       round((time_module.monotonic() - started) * 1000), None)
    return {"rows": len(evidence.rows), "taxonomies": len(grouped), "opened": opened, "closed": closed}


async def job_tdx_calendar_ipo(deps: ArchiveDeps, state: ArchiveState, day: date, now: datetime) -> dict[str, Any]:
    """Archive declared holiday and IPO application dates, normally dozens of calendar rows and a small IPO set."""
    started = time_module.monotonic()
    calendar = await tdx_reference_files.fetch_trade_calendar()
    ipo = await tdx_reference_files.fetch_ipo_calendar()
    calendar_rows = [{**row, "observation_symbol": f"holiday:{row['calendar_date']}",
                      "effective_at": _close_of(_tdx_date(row["calendar_date"])).isoformat(),
                      "available_at": now.isoformat()} for row in calendar.rows]
    ipo_rows = [{**row, "ts_code": row["symbol"],
                 "effective_at": _close_of(_tdx_date(row["apply_date"])).isoformat(),
                 "available_at": now.isoformat()} for row in ipo.rows]
    stored_calendar = await deps.collector.persist_observations("tdx_public", "tdx_holidays", calendar_rows)
    stored_ipo = await deps.collector.persist_observations("tdx_public", "tdx_ipo_subscriptions", ipo_rows)
    await deps.collector.record_health("tdx_public", "tdx_calendar_ipo", True, len(calendar_rows) + len(ipo_rows),
                                       round((time_module.monotonic() - started) * 1000), None)
    return {"calendar_rows": len(calendar_rows), "ipo_rows": len(ipo_rows),
            "stored_calendar": stored_calendar, "stored_ipo": stored_ipo}


RUNNERS: dict[str, Callable[[ArchiveDeps, ArchiveState, date, datetime], Awaitable[dict[str, Any]]]] = {
    "eastmoney_pools": job_eastmoney_pools, "eastmoney_change_summary": job_eastmoney_change_summary,
    "sentiment_close": job_sentiment_close, "fuyao_attention_close": job_fuyao_attention_close,
    "fuyao_dragon_tiger": job_fuyao_dragon_tiger, "eastmoney_datacenter": job_eastmoney_datacenter,
    "eastmoney_margin": job_eastmoney_margin, "fuyao_valuation_index": job_fuyao_valuation_index,
    "daily_valuation_projection": job_daily_valuation_projection,
    "tick_flow": job_tick_flow, "capital_changes": job_capital_changes,
    "tdx_security_list": job_tdx_security_list, "tdx_tipinfo": job_tdx_tipinfo, "tdx_gpcw": job_tdx_gpcw,
    "tdx_index_bars": job_tdx_index_bars,
    "tdx_mac_boards": job_tdx_mac_boards,
    "tdx_limit_pools": job_tdx_limit_pools,
    "tdx_host_probe": job_tdx_host_probe,
    "tdx_stat_snapshot": job_tdx_stat_snapshot, "tdx_files_membership": job_tdx_files_membership,
    "tdx_calendar_ipo": job_tdx_calendar_ipo,
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
        if job.key == 'daily_valuation_projection' and deps.project_valuations is None:
            continue
        state.last_attempt[job.key] = time_module.monotonic()
        try:
            results[job.key] = {"status": "completed", **await RUNNERS[job.key](deps, state, day, now)}
            if results[job.key]["status"] == "completed":
                state.done[job.key] = day.isoformat()
                await deps.collector.record_health("public_archive", job.key, True, 1, None, None)
        except NotYetPublished as pending:
            results[job.key] = {"status": "pending", "reason": error_text(pending, 240)}
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
    "ArchiveDeps", "ArchiveJob", "ArchiveState", "DATACENTER_EVENT_REPORTS", "JOBS", "NotYetPublished", "RUNNERS",
    "due_jobs", "run_due_jobs", "run_loop",
]
