"""The session's exchange-published limit prices, from Tencent's public quotes.

This is the ``limits.prices`` source the intraday strategies read before the
first scan of a session; it replaced Tushare's ``stk_limit`` cross-section on
2026-10-09 (decision 0005).  Tencent relays the exchange's own 涨停价/跌停价
in quote fields 47/48, which a rule cannot always reproduce: 920438.BJ closed
at 98.85 on 2026-10-08, and +30% is 128.505 - published as 128.50, because
the BSE rounds inward, where a half-up rule says 128.51.

The contract is the old one: the whole market or an error.  A failed batch, a
short answer, or quotes still dated the previous session (before about 09:15)
raise, so the caller stores nothing and the next scan retries.  The quote
fetch is injected - the same batched client the Longhu close uses - so this
module holds no session and opens no connection.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from datetime import date
from typing import Any

PROVIDER_KEY = "tencent_free"
#: Below this share of the requested symbols the answer is treated as short.
MINIMUM_COVERAGE = 0.95


class LimitCrossSectionUnavailable(RuntimeError):
    """No complete, session-dated limit cross-section could be read; callers degrade, not fail."""


async def session_limit_cross_section(
    trading_date: date, *,
    universe_symbols: Callable[[], Awaitable[Sequence[str]]],
    fetch_quotes: Callable[[list[str]], Awaitable[tuple[list[dict[str, Any]], dict[str, Any]]]],
) -> tuple[list[dict[str, Any]], str]:
    """``stk_limit``-shaped rows for ``trading_date`` and the provider that served them."""
    symbols = sorted({str(symbol).upper() for symbol in await universe_symbols()})
    if not symbols:
        raise LimitCrossSectionUnavailable("no universe to ask Tencent for limit prices")
    rows, health = await fetch_quotes(symbols)
    if health.get("errors"):
        raise LimitCrossSectionUnavailable(f"Tencent limit prices: {len(health['errors'])} batch(es) failed, "
                           f"first {health['errors'][0]}")
    stamp = trading_date.strftime("%Y%m%d")
    limits = [{
        "ts_code": row["ts_code"], "trade_date": stamp,
        "up_limit": row["up_limit"], "down_limit": row["down_limit"], "pre_close": row.get("pre_close"),
        "derivation": "exchange_published_via_tencent_quote",
    } for row in rows if row.get("trade_date") == stamp and row.get("up_limit") and row.get("down_limit")]
    if len(limits) < MINIMUM_COVERAGE * len(symbols):
        dated = sum(1 for row in rows if row.get("trade_date") == stamp)
        raise LimitCrossSectionUnavailable(f"Tencent limit prices for {trading_date}: {len(limits)} of {len(symbols)} symbols "
                           f"({dated} quotes dated that session); retrying on the next scan")
    return limits, PROVIDER_KEY


__all__ = ["LimitCrossSectionUnavailable", "MINIMUM_COVERAGE", "PROVIDER_KEY", "session_limit_cross_section"]
