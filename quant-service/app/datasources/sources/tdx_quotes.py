"""Legacy 0x053e quote readers: the board index quote and the stock order book.

A 0x053e price is an integer whose decimal point belongs to each security, and ``tdx_protocol.parse_quotes`` divides it
by 100. The security list confirms two decimals for stocks (main board, ChiNext, STAR; a BJ stock quotes with two too,
though its list row carries none) and for board codes, but not for ETFs, convertible bonds or funds
(scripts/data/tdx_quote_scale_and_bj_2026-10-10_mac.json). Each reader therefore takes only the instrument types it
serves and rejects any other code before connecting; the MAC batch quote (tdx_mac) serves ETFs, convertible bonds and
funds with float prices.
"""

from __future__ import annotations

from typing import Any, Sequence

from ..contracts import CapabilityEvidence
from . import tdx_instruments, tdx_protocol

_BOARDS = frozenset({"board"})
_BOOK_FIELDS = tuple(f"{name}{level}" for level in range(1, 6) for name in ("bid", "ask", "bid_vol", "ask_vol"))


def _board_row(quote: dict[str, Any]) -> dict[str, Any]:
    return {"symbol": tdx_protocol.symbol(quote["market"], quote["code"]),
            "last_price": quote["price"], "pre_close": quote["last_close"],
            "pct_change": None if quote["last_close"] == 0 else (quote["price"] / quote["last_close"] - 1) * 100,
            "volume_raw": quote["volume_lots"], "amount_raw": quote["amount"]}


def _book_row(quote: dict[str, Any]) -> dict[str, Any]:
    return {"symbol": tdx_protocol.symbol(quote["market"], quote["code"]), **{name: quote[name] for name in _BOOK_FIELDS}}


async def fetch_index_quote(*, symbols: Sequence[str]) -> CapabilityEvidence:
    """Board index quotes (880xxx and 881xxx codes). The bid and ask fields of a board code are not a book, so they are
    not read; ``pct_change`` is computed from the last price and the previous close, None when that is 0; volume and
    amount keep raw names because no evidence gives their units."""
    stocks = tdx_instruments.requested_of_types(symbols, _BOARDS)
    quotes, host = await tdx_protocol.call(lambda client: client.quotes(stocks), handshake_profile="login_one")
    return tdx_protocol.batch_evidence([_board_row(quote) for quote in quotes], symbols, host)


async def fetch_order_book(*, symbols: Sequence[str]) -> CapabilityEvidence:
    """The five-level book of stocks: bid1..5 and ask1..5 in yuan, bid_vol1..5 and ask_vol1..5 in lots. In the evidence
    it equals Tencent's closing book level by level (scripts/data/tdx_quote_order_book_2026-10-10_mac.json). Index,
    board and fund codes have no book and are refused."""
    stocks = tdx_instruments.requested_of_types(symbols, tdx_instruments.STOCK_TYPES)
    quotes, host = await tdx_protocol.call(lambda client: client.quotes(stocks), handshake_profile="login_one")
    return tdx_protocol.batch_evidence([_book_row(quote) for quote in quotes], symbols, host)


__all__ = ["fetch_index_quote", "fetch_order_book"]
