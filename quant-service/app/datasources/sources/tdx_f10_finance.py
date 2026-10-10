"""Stdlib TDX F10 and historical financial-file client.

The command builders/parsers are deliberately independent of the quote client;
``TdxF10Client`` only reuses its connected socket and ``_exchange`` framing.
All monetary values in the 0x0010 summary are normalized to yuan, share counts
to shares, and per-share values to yuan/share.  The finance summary uses
千元 for monetary floats and 万股 for capital floats; these differ from the
GPCW field units and must not share one scale factor.
"""

from __future__ import annotations

import struct
import asyncio
from typing import Any

from . import tdx_protocol
from .tdx_fin_history import FINANCE_HOSTS


FINANCE_FIELDS = (
    "float_shares", "province", "industry", "updated_date", "ipo_date",
    "total_shares", "state_shares", "sponsor_legal_shares", "legal_shares",
    "b_shares", "h_shares", "eps", "total_assets", "current_assets",
    "fixed_assets", "intangible_assets", "shareholder_count",
    "current_liabilities", "long_term_liabilities", "capital_reserve",
    "parent_equity", "operating_revenue", "main_business_profit",
    "accounts_receivable", "operating_profit", "investment_income",
    "net_cash_flow", "total_cash_inflow", "inventory", "total_profit",
    "after_tax_profit", "net_profit", "undistributed_profit",
    "net_assets_per_share", "reserved2",
)

FINANCE_METADATA_FIELDS = {"province", "industry", "updated_date", "ipo_date"}
FINANCE_SHARE_FIELDS = {
    "float_shares", "total_shares", "state_shares", "sponsor_legal_shares",
    "legal_shares", "b_shares", "h_shares",
}
FINANCE_PER_SHARE_FIELDS = {"eps", "net_assets_per_share", "reserved2"}
FINANCE_COUNT_FIELDS = {"shareholder_count"}


def build_finance_info_request(market: int, code: str) -> bytes:
    raw_code = code.encode("ascii")
    if len(raw_code) != 6 or not raw_code.isdigit():
        raise ValueError("TDX codes are six ASCII digits")
    if market not in (0, 1, 2):
        raise ValueError("market must be 0 (SZ), 1 (SH), or 2 (BJ)")
    return bytes.fromhex("0c 1f 18 76 00 01 0b 00 0b 00 10 00 01 00") + struct.pack("<B6s", market, raw_code)


def build_company_categories_request(market: int, code: str) -> bytes:
    raw_code = code.encode("ascii")
    if len(raw_code) != 6 or not raw_code.isdigit():
        raise ValueError("TDX codes are six ASCII digits")
    if market not in (0, 1, 2):
        raise ValueError("market must be 0 (SZ), 1 (SH), or 2 (BJ)")
    return bytes.fromhex("0c 0f 10 9b 00 01 0e 00 0e 00 cf 02") + struct.pack("<H6sI", market, raw_code, 0)


def build_company_content_request(market: int, code: str, filename: str, start: int, length: int) -> bytes:
    raw_code = code.encode("ascii")
    if len(raw_code) != 6 or not raw_code.isdigit():
        raise ValueError("TDX codes are six ASCII digits")
    if market not in (0, 1, 2):
        raise ValueError("market must be 0 (SZ), 1 (SH), or 2 (BJ)")
    name = filename.encode("ascii")
    if len(name) > 80 or start < 0 or length < 0 or length > 0xFFFFFFFF:
        raise ValueError("invalid F10 filename/range")
    return bytes.fromhex("0c 07 10 9c 00 01 68 00 68 00 d0 02") + struct.pack(
        "<H6sH80sIII", market, raw_code, 0, name.ljust(80, b"\0"), start, length, 0
    )


def _fixed(raw: bytes, encoding: str = "gbk") -> str:
    return raw.split(b"\0", 1)[0].decode(encoding, errors="replace").strip()


def parse_finance_info(body: bytes) -> dict[str, Any]:
    if len(body) < 2 + 7:
        raise ValueError("short finance response")
    count, market = struct.unpack_from("<HB", body, 0)
    code = _fixed(body[3:9], "ascii")
    fmt = "<fHHII" + "f" * 30
    size = struct.calcsize(fmt)
    if len(body) < 9 + size:
        raise ValueError("short finance record")
    values = struct.unpack_from(fmt, body, 9)
    result: dict[str, Any] = {"count": count, "market": market, "code": code}
    for name, value in zip(FINANCE_FIELDS, values):
        if name in {"province", "industry", "updated_date", "ipo_date"}:
            result[name] = value
        elif name in FINANCE_PER_SHARE_FIELDS:
            result[name] = float(value)
        elif name in FINANCE_COUNT_FIELDS:
            result[name] = float(value)
        elif name in FINANCE_SHARE_FIELDS:
            result[name] = float(value) * 10000.0
        else:
            result[name] = float(value) * 1000.0
    result["field_units"] = {
        name: ("股" if name in FINANCE_SHARE_FIELDS else "元/股" if name in FINANCE_PER_SHARE_FIELDS
               else "户" if name in FINANCE_COUNT_FIELDS else "code" if name in {"province", "industry"}
               else "YYYYMMDD" if name in {"updated_date", "ipo_date"}
               else "元") for name in FINANCE_FIELDS
    }
    result["units"] = {"shares": "股", "amounts": "元", "per_share": "元/股", "eps": "元/股",
                       "finance_raw_money": "千元", "finance_raw_shares": "万股"}
    return result


def parse_company_categories(body: bytes) -> list[dict[str, Any]]:
    if len(body) < 2:
        return []
    count = struct.unpack_from("<H", body)[0]
    rows = []
    for index in range(count):
        pos = 2 + index * 152
        if pos + 152 > len(body):
            break
        name, filename, start, length = struct.unpack_from("<64s80sII", body, pos)
        rows.append({"name": _fixed(name), "filename": _fixed(filename, "ascii"), "start": start, "length": length})
    return rows


def parse_company_content(body: bytes) -> str:
    if len(body) < 12:
        return ""
    length = struct.unpack_from("<H", body, 10)[0]
    return body[12:12 + length].decode("gbk", errors="replace")


def parse_report_file(body: bytes) -> tuple[int, bytes]:
    if len(body) < 4:
        return 0, b""
    size = struct.unpack_from("<I", body)[0]
    return size, body[4:4 + size]


class TdxF10Client(tdx_protocol.TdxClient):
    """Connected TDX client for F10 and finance summary."""

    def finance_info(self, market: int, code: str) -> dict[str, Any]:
        return parse_finance_info(self._exchange(build_finance_info_request(market, code)))

    def company_categories(self, market: int, code: str) -> list[dict[str, Any]]:
        return parse_company_categories(self._exchange(build_company_categories_request(market, code)))

    def company_content(self, market: int, code: str, filename: str, start: int, length: int) -> str:
        return parse_company_content(self._exchange(build_company_content_request(market, code, filename, start, length)))


def finance_info(market: int, code: str, *, timeout_seconds: float = 5.0) -> dict[str, Any]:
    result, _ = tdx_protocol.call_sync(lambda client: client.finance_info(market, code), timeout_seconds=timeout_seconds)
    return result


async def fetch_financial_summary(*, symbol: str) -> list[dict[str, Any]]:
    """The latest 0x0010 summary. It carries no report period: ``updated_date`` keeps moving after the
    disclosure (delta-1 D4), so it is neither the period nor ``available_at``. The period and the first
    disclosure date come from tipinfo columns 2 and 4 (delta-3 Q1/Q2), joined by the I4 collector."""
    market, code = tdx_protocol.market_code(symbol)
    row = await asyncio.to_thread(finance_info, market, code)
    return [{"symbol": symbol, "report_period": None,
             "statement_items": {name: row[name] for name in FINANCE_FIELDS},
             "field_units": dict(row["field_units"])}]


def _company_profile_sync(symbol: str) -> list[dict[str, Any]]:
    market, code = tdx_protocol.market_code(symbol)

    def fetch(client: TdxF10Client) -> list[dict[str, Any]]:
        categories = client.company_categories(market, code)
        return [{"symbol": symbol, "category": category["name"], "filename": category["filename"],
                 "content": client.company_content(market, code, category["filename"], category["start"], category["length"])}
                for category in categories]

    result, _ = tdx_protocol.call_sync(fetch)
    return result


async def fetch_company_profile(*, symbol: str) -> list[dict[str, Any]]:
    return await asyncio.to_thread(_company_profile_sync, symbol)


__all__ = [
    "FINANCE_FIELDS", "FINANCE_HOSTS", "TdxF10Client", "build_company_categories_request",
    "build_company_content_request", "build_finance_info_request",
    "finance_info", "fetch_company_profile", "fetch_financial_summary", "parse_company_categories",
    "parse_company_content", "parse_finance_info",
]
