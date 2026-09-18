"""The Tushare-compatible ``stk_limit`` cross-section behind ``limits.prices``.

The request contract is this source's, not the strategy's: the whole-market
response must be paginated (asking for it at once is refused outright by the
one route that still serves it -- the strategy sat blocked all of
2026-09-17), and a page loop that stops early must fail rather than return a
short table (2026-09-16 stored 2,359 of ~5,700 names).  The API caller is
injected, so this module holds no credentials and opens no connection.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import date
from typing import Any


TRADE_LIMIT_PAGE_SIZE = 2000
TRADE_LIMIT_MAX_ROWS = 12000
TRADE_LIMIT_MAX_PAGES = 8


async def fetch_limit_cross_section(
    call_api: Callable[..., Awaitable[Any]], trading_date: date,
) -> tuple[list[dict[str, Any]], str]:
    """The session's limit prices and the provider key that served them."""
    call = await call_api(
        "stk_limit", {"trade_date": trading_date.strftime("%Y%m%d")}, None, "auto",
        paginate=True, page_size=TRADE_LIMIT_PAGE_SIZE, max_rows=TRADE_LIMIT_MAX_ROWS,
        max_pages=TRADE_LIMIT_MAX_PAGES, require_complete=True,
    )
    rows = [dict(row) for row in call.rows if str(row.get("ts_code") or "").strip()]
    return rows, str(call.provider.key)


__all__ = ["TRADE_LIMIT_MAX_PAGES", "TRADE_LIMIT_MAX_ROWS", "TRADE_LIMIT_PAGE_SIZE", "fetch_limit_cross_section"]
