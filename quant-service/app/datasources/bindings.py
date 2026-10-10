"""Bind this package's adapters to their catalogued capabilities.

Parameter convention per capability (every source of a capability accepts
the same keywords, so the resolver can fall through without translation):

* ``limits.*`` pools, ``limits.seal_detail`` ... ``trade_date: date``
* ``limits.anomaly_tape``, ``sector.anomaly``, ``attention.*`` lists: none
* ``attention.em_rank_history``, ``fundamentals.capital_changes``: ``symbol``
* ``ticks.session``, ``auction.history_0925``: ``symbol`` and ``trade_date``
* ``events.*`` (datacenter): ``start: date``, ``end: date``
* ``news.flash``: none;  ``events.investor_qa``: ``observed_at``
* ``fund.nav``: ``fund_code``
* ``quote.watch_snapshot``: ``symbols``
* ``limits.prices``: ``symbols``
* ``bars.daily``: ``symbol``, ``count``
* ``bars.minute``: ``symbol``, ``count`` (MAC 1-minute bars)
* ``quote.valuation``, ``fundamentals.daily_basic``: ``symbols`` (optional symbol filter)
* ``sector.board_catalog``: no parameters; returns the MAC board types of ``tdx_mac.BOARD_TYPES``
* ``sector.membership``: no parameters for the TDX file snapshot; ``sector_key`` for MAC
* ``reference.trade_calendar``, ``events.ipo_calendar``: no parameters

Sources that live outside this package (licensed gateways, the existing
Tencent/Sina/Eastmoney quote paths) are bound by the composition root.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from datetime import date, datetime, time, timezone
from typing import Any
from zoneinfo import ZoneInfo

from .derived.tick_flow import summarize_ticks
from .resolver import CapabilityResolver
from .sources import eastmoney_datacenter, eastmoney_hot_rank, eastmoney_ztb, investor_qa, news_flash, ticks, ttfund, xuangubao_pool
from .sources.fuyao_evidence import fetch_all_pool_pages


CN_TZ = ZoneInfo("Asia/Shanghai")
FuyaoFetch = Callable[[str, dict[str, Any]], Awaitable[Mapping[str, Any]]]


def _pool(pool: str) -> Callable[..., Awaitable[list[dict[str, Any]]]]:
    async def fetch(*, trade_date: date) -> list[dict[str, Any]]:
        return await eastmoney_ztb.fetch_pool(pool, trade_date)
    return fetch


def _report(key: str) -> Callable[..., Awaitable[list[dict[str, Any]]]]:
    async def fetch(*, start: date, end: date) -> list[dict[str, Any]]:
        return await eastmoney_datacenter.fetch_report(key, start=start, end=end)
    return fetch


def _period_report(key: str) -> Callable[..., Awaitable[list[dict[str, Any]]]]:
    async def fetch(*, period: date) -> list[dict[str, Any]]:
        return await eastmoney_datacenter.period_report(key, period)
    return fetch


def _fuyao_pool(fuyao_fetch: FuyaoFetch, capability: str) -> Callable[..., Awaitable[list[dict[str, Any]]]]:
    async def fetch(*, trade_date: date) -> list[dict[str, Any]]:
        today = datetime.now(timezone.utc).astimezone(CN_TZ).date()
        if trade_date != today:
            # The pool routes default to the current session; ``date_ms`` asks
            # for an earlier one (Shanghai midnight of that date).
            midnight = datetime.combine(trade_date, time(0, 0), CN_TZ)
            async def dated(route: str, params: dict[str, Any]) -> Mapping[str, Any]:
                return await fuyao_fetch(route, {**params, "date_ms": int(midnight.timestamp() * 1000)})
            data = await fetch_all_pool_pages(dated, capability)
        else:
            data = await fetch_all_pool_pages(fuyao_fetch, capability)
        return [dict(item) for item in data.get("item") or []]
    return fetch


def _fuyao_list(fuyao_fetch: FuyaoFetch, route: str, params: dict[str, Any]) -> Callable[..., Awaitable[list[dict[str, Any]]]]:
    async def fetch() -> list[dict[str, Any]]:
        data = await fuyao_fetch(route, dict(params))
        return [dict(item) for item in data.get("item") or []]
    return fetch


def register_package_sources(resolver: CapabilityResolver, *, fuyao_fetch: FuyaoFetch | None = None) -> CapabilityResolver:
    """Bind every adapter implemented in this package; returns ``resolver``."""
    for capability, pool in (
        ("limits.limit_up_pool", "limit_up"), ("limits.seal_detail", "limit_up"), ("limits.broken_pool", "broken"),
        ("limits.limit_down_pool", "limit_down"), ("limits.previous_limit_up", "previous_limit_up"),
        ("limits.strong_pool", "strong"), ("limits.sub_new_pool", "sub_new"),
    ):
        resolver.bind("eastmoney_ztb", capability, _pool(pool))
    resolver.bind("eastmoney_ztb", "limits.anomaly_tape", lambda: eastmoney_ztb.fetch_stock_changes())

    async def board_changes() -> list[dict[str, Any]]:
        rows, _stamp = await eastmoney_ztb.fetch_board_changes()
        return rows
    resolver.bind("eastmoney_ztb", "sector.anomaly", board_changes)

    resolver.bind("eastmoney_hot_rank", "attention.em_popularity", lambda: eastmoney_hot_rank.fetch_rank_list("popularity"))
    resolver.bind("eastmoney_hot_rank", "attention.em_surge", lambda: eastmoney_hot_rank.fetch_rank_list("surge"))
    resolver.bind("eastmoney_hot_rank", "attention.em_rank_history",
                  lambda *, symbol: eastmoney_hot_rank.fetch_rank_history(symbol))

    for capability, report in (
        ("events.restricted_release", "restricted_release"), ("events.holder_trade", "holder_trade"),
        ("events.holder_count", "holder_count"), ("events.block_trade", "block_trade"),
        ("events.earnings_forecast", "earnings_forecast"), ("events.earnings_express", "earnings_express"),
        ("events.repurchase", "repurchase"), ("events.ipo_calendar", "ipo_calendar"),
    ):
        resolver.bind("eastmoney_datacenter", capability, _report(report))
    resolver.bind("eastmoney_datacenter", "events.disclosure_schedule", _period_report("disclosure_schedule"))
    resolver.bind("xuangubao", "limits.seal_detail",
                  lambda *, trade_date: xuangubao_pool.fetch_pool("limit_up", trade_date))
    resolver.bind("eastmoney_datacenter", "reference.suspensions",
                  lambda *, trade_date: eastmoney_datacenter.suspensions_on(trade_date))

    for source, fetch in news_flash.FETCHERS.items():
        resolver.bind(source, "news.flash", fetch)
    resolver.bind("cninfo_irm", "events.investor_qa", lambda *, observed_at: investor_qa.fetch_cninfo())
    resolver.bind("sse_einteract", "events.investor_qa", lambda *, observed_at: investor_qa.fetch_sse(observed_at))

    async def tdx_ticks(*, symbol: str, trade_date: date) -> list[Any]:
        rows, _host = await ticks.fetch_tdx_ticks(symbol, trade_date)
        return rows

    async def tencent_ticks(*, symbol: str, trade_date: date) -> list[Any]:
        today = datetime.now(timezone.utc).astimezone(CN_TZ).date()
        return await ticks.fetch_tencent_ticks(symbol) if trade_date == today else []

    async def capital_changes(*, symbol: str) -> list[dict[str, Any]]:
        rows, _host = await ticks.fetch_tdx_capital_changes(symbol)
        return rows

    async def opening_auction(*, symbol: str, trade_date: date) -> list[dict[str, Any]]:
        rows, _host = await ticks.fetch_tdx_ticks(symbol, trade_date)
        summary = summarize_ticks(rows)
        if summary["opening_auction"] is None:
            return []
        return [{"symbol": symbol, "trade_date": trade_date.isoformat(), **summary["opening_auction"],
                 "auction_curve": summary["auction_curve"]}]

    resolver.bind("tdx_public", "ticks.session", tdx_ticks)
    resolver.bind("tdx_public", "auction.history_0925", opening_auction)
    resolver.bind("tencent_free", "ticks.session", tencent_ticks)
    resolver.bind("tdx_public", "fundamentals.capital_changes", capital_changes)

    async def eastmoney_capital(*, symbol: str) -> list[dict[str, Any]]:
        return await eastmoney_datacenter.fetch_report("share_capital", extra_filter=f'(SECUCODE="{symbol}")', max_pages=2)
    resolver.bind("eastmoney_datacenter", "fundamentals.capital_changes", eastmoney_capital)
    resolver.bind("ttfund", "fund.nav", lambda *, fund_code: ttfund.fetch_nav_history(fund_code))

    if fuyao_fetch is not None:
        for capability, route in (
            ("limits.limit_up_pool", "a_share_limit_up_pool"), ("limits.broken_pool", "a_share_limit_break_pool"),
            ("limits.limit_down_pool", "a_share_limit_down_pool"),
        ):
            resolver.bind("fuyao_ths", capability, _fuyao_pool(fuyao_fetch, route))
        resolver.bind("fuyao_ths", "attention.ths_hot_rank", _fuyao_list(fuyao_fetch, "a_share_hot_stock_list", {"period": "day"}))
        resolver.bind("fuyao_ths", "attention.ths_skyrocket", _fuyao_list(fuyao_fetch, "a_share_skyrocket_list", {"period": "day"}))
        resolver.bind("fuyao_ths", "limits.stock_anomaly_reason", _fuyao_list(fuyao_fetch, "a_share_anomaly_analysis_list", {}))
        resolver.bind("fuyao_ths", "auction.short_term_benchmark",
                      _fuyao_list(fuyao_fetch, "a_share_auction_short_term_benchmark", {}))
    return resolver


__all__ = ["register_package_sources"]
