"""Legacy TDX daily, minute and index bars."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from ..contracts import CapabilityEvidence
from . import tdx_instruments, tdx_protocol


BAR_PAGE_SIZE = tdx_protocol.MAX_BARS_PER_REQUEST
CN_TZ = ZoneInfo("Asia/Shanghai")


def _bar_pages(client: tdx_protocol.TdxClient, category: int, market: int, code: str, count: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for start in range(0, count, BAR_PAGE_SIZE):
        page = tdx_protocol.parse_bars(
            category,
            client._exchange(tdx_protocol.build_bars_request(category, market, code, start, BAR_PAGE_SIZE)),
        )
        rows.extend(page)
        if len(page) < BAR_PAGE_SIZE:
            break
    unique = {row["datetime"]: row for row in rows}
    return sorted(unique.values(), key=lambda row: row["datetime"])[-count:]


async def _fetch_index_bars(symbol: str, count: int) -> CapabilityEvidence:
    market, code = tdx_protocol.market_code(symbol)
    if tdx_instruments.instrument_type(market, code) not in ("index", "board"):
        raise ValueError("index bars require an index or board symbol")
    if not 1 <= count <= BAR_PAGE_SIZE:
        raise ValueError("count must be between 1 and 800")
    canonical = tdx_protocol.symbol(market, code)
    rows, host = await tdx_protocol.call(
        lambda client: tdx_instruments.index_bars(client, market, code, 0, count), handshake_profile="login_one"
    )
    normalized = [
        {
            "symbol": canonical,
            "trade_date": row["datetime"],
            "open": row["open"],
            "high": row["high"],
            "low": row["low"],
            "close": row["close"],
            "amount": row["amount"],
            "volume_raw": row["volume"],
            "up_count": row["up_count"],
            "down_count": row["down_count"],
        }
        for row in rows
    ]
    return tdx_protocol.observed_evidence(normalized, host)


async def fetch_index_daily(*, symbol: str, count: int) -> CapabilityEvidence:
    return await _fetch_index_bars(symbol, count)


async def fetch_index_breadth(*, symbol: str, count: int) -> CapabilityEvidence:
    evidence = await _fetch_index_bars(symbol, count)
    return CapabilityEvidence(
        [{key: row[key] for key in ("symbol", "trade_date", "up_count", "down_count")} for row in evidence.rows],
        coverage=evidence.coverage,
        effective_at_min=evidence.effective_at_min,
        effective_at_max=evidence.effective_at_max,
        available_at_min=evidence.available_at_min,
        available_at_max=evidence.available_at_max,
        warnings=evidence.warnings,
    )


def _normalize_bar(symbol: str, row: dict[str, Any], *, minute: bool) -> dict[str, Any]:
    normalized = {
        "symbol": symbol,
        "open": row["open"],
        "high": row["high"],
        "low": row["low"],
        "close": row["close"],
        "volume": row["volume"],
        "amount": row["amount"],
    }
    if minute:
        normalized["bar_time"] = datetime.strptime(row["datetime"], "%Y-%m-%d %H:%M").replace(tzinfo=CN_TZ)
    else:
        normalized["trade_date"] = row["datetime"]
    return normalized


async def _fetch_bars(*, symbol: str, count: int, category: int, minute: bool) -> CapabilityEvidence:
    market, code = tdx_protocol.market_code(symbol)
    if count < 1:
        raise ValueError("count must be at least 1")
    canonical = tdx_protocol.symbol(market, code)
    rows, host = await tdx_protocol.call(
        lambda client: [_normalize_bar(canonical, row, minute=minute)
                        for row in _bar_pages(client, category, market, code, count)],
        handshake_profile="login_one",
    )
    return tdx_protocol.observed_evidence(rows, host)


async def fetch_daily(*, symbol: str, count: int) -> CapabilityEvidence:
    return await _fetch_bars(symbol=symbol, count=count, category=9, minute=False)


async def fetch_minute(*, symbol: str, count: int) -> CapabilityEvidence:
    return await _fetch_bars(symbol=symbol, count=count, category=8, minute=True)


__all__ = ["fetch_daily", "fetch_index_breadth", "fetch_index_daily", "fetch_minute"]
