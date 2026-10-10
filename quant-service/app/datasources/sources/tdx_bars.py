"""Legacy TDX daily, minute and index bars."""

from __future__ import annotations

from typing import Any

from ..contracts import CapabilityEvidence
from . import tdx_instruments, tdx_protocol


BAR_PAGE_SIZE = tdx_protocol.MAX_BARS_PER_REQUEST


def _index_pages(client: tdx_protocol.TdxClient, market: int, code: str, count: int) -> list[dict[str, Any]]:
    return tdx_instruments.parse_index_bars(
        client._exchange(tdx_protocol.build_bars_request(9, market, code, 0, count))
    )


async def _fetch_index_bars(symbol: str, count: int) -> CapabilityEvidence:
    market, code = tdx_protocol.market_code(symbol)
    if tdx_instruments.instrument_type(market, code) not in ("index", "board"):
        raise ValueError("index bars require an index or board symbol")
    if not 1 <= count <= BAR_PAGE_SIZE:
        raise ValueError("count must be between 1 and 800")
    canonical = tdx_protocol.symbol(market, code)
    rows, host = await tdx_protocol.call(
        lambda client: _index_pages(client, market, code, count), handshake_profile="login_one"
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
            "volume_raw": row["volume_raw"],
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


__all__ = ["fetch_index_breadth", "fetch_index_daily"]
