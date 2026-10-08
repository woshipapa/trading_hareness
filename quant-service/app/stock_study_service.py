"""Bounded multi-source assembly for the single-stock research endpoint.

The service composes independently bounded source probes.  It deliberately
keeps all results labelled by source and never promotes research evidence into
an executable strategy decision.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Awaitable, Callable


@dataclass(frozen=True)
class StockStudyDependencies:
    china_today: Callable[[], date]
    daily_sync_request: Callable[..., Any]
    sync_baostock: Callable[[Any], Awaitable[dict[str, Any]]]
    free_fetch: Callable[[str, str, str, Callable[[], Awaitable[Any]], str], Awaitable[tuple[dict[str, Any], Any]]]
    eastmoney_daily: Callable[[str, str, str], Awaitable[list[dict[str, Any]]]]
    eastmoney_quote: Callable[[str], Awaitable[dict[str, Any] | None]]
    run_akshare: Callable[..., Awaitable[Any]]
    akshare_daily: Callable[..., Any]
    tencent_daily: Callable[[str, str, str], Awaitable[list[dict[str, Any]]]]
    sina_quote: Callable[[str], Awaitable[dict[str, Any] | None]]
    cninfo_announcements: Callable[..., Awaitable[list[dict[str, Any]]]]
    run_database: Callable[..., Awaitable[Any]]
    persist_market_events: Callable[..., int]
    persist_announcement_health: Callable[..., None]
    technical_summary: Callable[[list[dict[str, Any]]], dict[str, Any]]
    analyst_claims: Callable[[str], tuple[list[dict[str, Any]], dict[str, Any]]]
    recent_events: Callable[[str, int], list[dict[str, Any]]]
    window_readiness: Callable[[str, date, date], dict[str, Any]]
    latest_row: Callable[[list[dict[str, Any]]], dict[str, Any] | None]
    read_persisted_factors: Callable[[str, date, date], Awaitable[list[dict[str, Any]]]] | None = None
    # The full-market close Longhu writes every session (canonical_bars_daily).
    read_daily_bars: Callable[[str, date, date], Awaitable[list[dict[str, Any]]]] | None = None


def _market_date(as_of: date) -> date:
    if as_of.weekday() == 6:
        return as_of - timedelta(days=2)
    if as_of.weekday() == 5:
        return as_of - timedelta(days=1)
    return as_of


async def build(symbol: str, request: Any, deps: StockStudyDependencies) -> dict[str, Any]:
    """Collect the existing bounded evidence set for a single stock study."""
    as_of = request.as_of_date or deps.china_today()
    market_date = _market_date(as_of)
    calendar_span = min(45, max(request.lookback_days + 12, 32))
    start_date = market_date - timedelta(days=calendar_span)
    start, end = start_date.strftime("%Y%m%d"), market_date.strftime("%Y%m%d")
    # The ten Tushare reads this used to make (daily, profile, valuation, limits, three
    # money flows, chips, factors and rt_min) were retired with Tushare on 2026-10-08.
    daily_rows = (await deps.read_daily_bars(symbol, start_date, market_date)
                  if deps.read_daily_bars is not None else [])
    persisted_factor_rows = (
        await deps.read_persisted_factors(symbol, start_date, market_date)
        if deps.read_persisted_factors is not None else []
    )
    baostock_task = asyncio.create_task(deps.sync_baostock(deps.daily_sync_request(trade_date=market_date, symbols=[symbol])))
    free_results = await asyncio.gather(
        deps.free_fetch("东方财富公开日线", "eastmoney_free", "daily_bar", lambda: deps.eastmoney_daily(symbol, start, end), symbol),
        deps.free_fetch("东方财富公开报价", "eastmoney_free", "realtime_quote", lambda: deps.eastmoney_quote(symbol), symbol),
        deps.free_fetch("AKShare公开日线", "akshare", "daily_bar", lambda: deps.run_akshare(deps.akshare_daily, symbol, start, end, timeout_seconds=12), symbol),
        deps.free_fetch("腾讯财经公开日线", "tencent_free", "daily_bar", lambda: deps.tencent_daily(symbol, start, end), symbol),
        deps.free_fetch("新浪财经公开报价", "sina_free", "realtime_quote", lambda: deps.sina_quote(symbol), symbol),
    )
    sources = [{
        "source": "本地收盘日线", "api_name": "daily", "provider": "canonical_bars_daily",
        "status": "completed" if daily_rows else "missing", "received": len(daily_rows), "stored": 0,
    }]
    sources.append({
        "source": "owner persisted adjustment factor",
        "api_name": "adj_factor",
        "provider": "owner_persisted_adjustment_factor",
        "status": "completed" if persisted_factor_rows else "missing",
        "received": len(persisted_factor_rows), "stored": 0,
    })
    sources.extend(result[0] for result in free_results)
    free_data = {result[0]["source"]: result[1] for result in free_results}
    try:
        baostock = await asyncio.wait_for(baostock_task, timeout=15)
    except asyncio.TimeoutError:
        baostock = {"status": "failed", "imported": 0, "failures": ["study source exceeded 15 second budget"]}
    sources.append({"source": "Baostock 日线", "api_name": "daily_bar", "provider": "baostock", "status": baostock["status"],
                    "received": baostock.get("imported", 0), "stored": baostock.get("imported", 0), "failures": baostock.get("failures", [])})

    announcement_started_at = asyncio.get_running_loop().time()
    try:
        announcement_rows = await asyncio.wait_for(
            deps.cninfo_announcements(symbol, start_date, market_date, max_pages=1), timeout=12,
        )
        announcement_stored = await deps.run_database(deps.persist_market_events, "cninfo_free", announcement_rows, timeout_seconds=60)
        await deps.run_database(
            deps.persist_announcement_health, "completed", announcement_stored, [],
            round((asyncio.get_running_loop().time() - announcement_started_at) * 1000),
        )
        sources.append({"source": "巨潮公开公告", "api_name": "announcement", "provider": "cninfo_free",
                        "status": "completed" if announcement_rows else "empty", "received": len(announcement_rows), "stored": announcement_stored})
    except Exception as error:  # noqa: BLE001 - bounded study reports source failure as evidence
        announcement_rows = []
        await deps.run_database(
            deps.persist_announcement_health, "failed", 0, [str(error)],
            round((asyncio.get_running_loop().time() - announcement_started_at) * 1000),
        )
        sources.append({"source": "巨潮公开公告", "api_name": "announcement", "provider": "cninfo_free",
                        "status": "failed", "received": 0, "stored": 0, "error": str(error)[:300]})

    technical = deps.technical_summary(daily_rows)
    claims, analyst = await deps.run_database(deps.analyst_claims, symbol)
    announcements = await deps.run_database(deps.recent_events, symbol, 20)
    technical_component = ((technical["score"] - 50) / 50) if technical.get("score") is not None else 0.0
    combined_score = round(max(0, min(100, 50 + technical_component * 25 + analyst["score"] * 25)), 1)
    stance = "research_positive" if combined_score >= 62 else "research_negative" if combined_score <= 38 else "mixed_or_insufficient"
    readiness = await deps.run_database(deps.window_readiness, symbol, start_date, market_date)
    return {
        "symbol": symbol, "as_of_date": str(market_date), "lookback_days": request.lookback_days, "sources": sources,
        "on_demand_readiness": readiness,
        "market": {
            "daily_bars": daily_rows[-45:], "latest_realtime": None,
            "eastmoney_quote": free_data["东方财富公开报价"], "eastmoney_daily_bars": free_data["东方财富公开日线"],
            "akshare_daily_bars": free_data["AKShare公开日线"], "tencent_daily_bars": free_data["腾讯财经公开日线"],
            "sina_quote": free_data["新浪财经公开报价"], "latest_adj_factor": deps.latest_row(persisted_factor_rows),
            # Retired with Tushare; kept so the response shape does not change.
            "latest_limit": None, "latest_daily_basic": None, "latest_moneyflow": None, "latest_ths_moneyflow": None,
            "latest_dc_moneyflow": None, "latest_chip": None, "latest_chip_distribution": None, "latest_factor": None,
            "profile": None,
        },
        "events": {"announcements": announcements, "provider": "cninfo_free", "decision_eligible": False},
        "technical": technical, "analyst": {"summary": analyst, "claims": claims},
        "combined": {
            "score": combined_score, "stance": stance,
            "notice": "研究结论基于当前可得数据与远端分析师证据，不构成交易指令。",
            "reasons": [*technical.get("reasons", [])[:3], f"远端分析师有效观点 {analyst['claim_count']} 条，聚合方向为 {analyst['direction']}"],
        },
    }


__all__ = ["StockStudyDependencies", "build"]
