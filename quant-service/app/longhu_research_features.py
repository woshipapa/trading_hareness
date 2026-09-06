"""Conservative normalisation for Longhu supplemental research responses.

The vendor uses several incompatible payload shapes.  This module keeps the
provider payload lossless and only derives a symbol when a strict six-digit
exchange code is present.  It intentionally does not guess field meanings.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any, Mapping


SYMBOL_RE = re.compile(r"(?:SZ|SH|BJ)?\d{6}|\d{6}\.(?:SZ|SH|BJ)", re.I)


def payload_rows(payload: Mapping[str, Any]) -> list[Any]:
    """Return the first recognised vendor collection, preserving row shape."""
    for key in ("list", "data", "items", "trend", "rows", "bid"):
        value = payload.get(key)
        if isinstance(value, list):
            return value
    return []


def strict_symbol(value: Any) -> str | None:
    text = str(value or "").strip().upper()
    match = re.fullmatch(r"(\d{6})\.(SH|SZ|BJ)", text)
    if match:
        return text
    match = re.fullmatch(r"(?:SH|SZ|BJ)?(\d{6})", text)
    if not match:
        return None
    code = match.group(1)
    exchange = "SH" if code.startswith("6") else "SZ" if code.startswith(("0", "3")) else "BJ" if code.startswith(("4", "8", "9")) else None
    return f"{code}.{exchange}" if exchange else None


def _row_symbol(row: Any) -> str | None:
    if not isinstance(row, Mapping):
        return None
    for key in ("ts_code", "symbol", "code", "StockID", "stock_id", "thscode"):
        symbol = strict_symbol(row.get(key))
        if symbol:
            return symbol
    return None


def normalize_payload(
    *, target: str, action: str, controller: str | None, payload: Mapping[str, Any],
    trade_date: date, observed_at: datetime,
) -> list[dict[str, Any]]:
    """Create raw evidence rows with explicit clocks and semantic boundaries."""
    rows = payload_rows(payload)
    source_rows = rows if rows else [payload]
    result: list[dict[str, Any]] = []
    for index, item in enumerate(source_rows):
        symbol = _row_symbol(item)
        result.append({
            "ts_code": symbol,
            "target": target,
            "action": action,
            "controller": controller,
            "exchange_date": trade_date.isoformat(),
            "observed_at": observed_at.isoformat(),
            "available_at": observed_at.isoformat(),
            "availability_basis": "owner_gateway_receipt_post_close",
            "research_only": True,
            "replay_only": True,
            "live_effect": "none",
            "record_index": index,
            "payload": item,
        })
    return result


__all__ = ["normalize_payload", "payload_rows", "strict_symbol"]
