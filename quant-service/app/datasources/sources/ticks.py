"""Tick (分笔) and capital-change fetchers built on Tencent and TDX.

* Tencent ``stock.gtimg.cn`` serves *today's* prints with second resolution
  and a B/S/M side, paged backwards-free from the open.
* TDX serves any recent session's prints (minute resolution, side codes
  verified against Tencent) and the full ex-rights / share-capital log.

The share-capital log is what lets the float share count be read per date
instead of accumulated from daily snapshots.
"""

from __future__ import annotations

from datetime import date, datetime, time
from typing import Any
from zoneinfo import ZoneInfo

from . import tdx_protocol
from ..http import request_text
from ..derived.tick_flow import Tick, parse_tencent_detail, summarize_ticks, ticks_from_tdx


CN_TZ = ZoneInfo("Asia/Shanghai")
TENCENT_TICK_PROVIDER_KEY = "tencent_free"
MAX_TENCENT_PAGES = 150


def _tencent_code(symbol: str) -> str:
    code, _, exchange = symbol.upper().partition(".")
    return f"{exchange.lower()}{code}"


async def fetch_tencent_ticks(symbol: str, *, max_pages: int = MAX_TENCENT_PAGES) -> list[Tick]:
    """All of today's Tencent prints for one symbol, oldest first."""
    ticks: list[Tick] = []
    for page in range(max_pages):
        text = await request_text("GET", "http://stock.gtimg.cn/data/index.php", params={
            "appn": "detail", "action": "data", "c": _tencent_code(symbol), "p": str(page),
        })
        rows = parse_tencent_detail(text)
        if not rows:
            break
        ticks.extend(rows)
    return ticks


async def fetch_tdx_ticks(symbol: str, trade_date: date) -> tuple[list[Tick], str]:
    """One session's TDX prints (works for today once the session closed)."""
    market, code = tdx_protocol.market_code(symbol)
    rows, host = await tdx_protocol.call(lambda client: client.ticks(market, code, trade_date))
    return ticks_from_tdx(rows), host


async def fetch_tdx_capital_changes(symbol: str) -> tuple[list[dict[str, Any]], str]:
    market, code = tdx_protocol.market_code(symbol)
    return await tdx_protocol.call(lambda client: client.xdxr(market, code))


def tick_flow_observation(symbol: str, trade_date: date, ticks: list[Tick], *, source: str,
                          observed_at: datetime) -> dict[str, Any]:
    """One per-symbol daily tick-flow record, effective at that close."""
    summary = summarize_ticks(ticks)
    effective = datetime.combine(trade_date, time(15, 0), CN_TZ)
    return {
        "ts_code": symbol, "trade_date": trade_date.isoformat(), "tick_source": source,
        "effective_at": min(effective, observed_at).isoformat(), "available_at": observed_at.isoformat(),
        **summary,
    }


def capital_change_observations(symbol: str, rows: list[dict[str, Any]], observed_at: datetime) -> list[dict[str, Any]]:
    """Share-capital and dividend records; future-dated rows stay future-effective.

    ``effective_at`` is the event date (an announced ex-date can be ahead of
    the capture), ``available_at`` the capture time, so a replay only sees a
    record once it was actually fetched.
    """
    result = []
    for row in rows:
        try:
            day = date.fromisoformat(str(row["date"]))
        except (KeyError, ValueError):
            continue
        result.append({
            "ts_code": symbol, **row,
            "effective_at": datetime.combine(day, time(9, 0), CN_TZ).isoformat(),
            "available_at": observed_at.isoformat(),
        })
    return result


def float_shares_on(rows: list[dict[str, Any]], on: date) -> float | None:
    """Float shares (in 10k) effective on ``on`` from the TDX capital log."""
    latest = None
    for row in sorted(rows, key=lambda item: str(item.get("date"))):
        if str(row.get("date")) > on.isoformat():
            break
        after = row.get("float_shares_after_10k")
        if after:
            latest = float(after)
    return latest


__all__ = [
    "TENCENT_TICK_PROVIDER_KEY", "capital_change_observations", "fetch_tdx_capital_changes",
    "fetch_tdx_ticks", "fetch_tencent_ticks", "float_shares_on", "tick_flow_observation",
]
