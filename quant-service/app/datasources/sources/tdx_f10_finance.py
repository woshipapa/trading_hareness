"""TDX F10 company text and the 0x0010 finance summary.

The command builders and parsers are pure.  The two ``fetch_*`` adapters send build -> ``client._exchange`` -> parse
through :func:`tdx_protocol.call`, like :mod:`tdx_microstructure`: no client subclass, host failover from the shared
pool, and the answering host on the evidence.  The summary arrives in 千元 (money) and 万股 (share capital); they are
normalized here to yuan and shares.  That differs from the GPCW files, whose money is already yuan, so the two must
not share one scale factor.
"""

from __future__ import annotations

import struct
from typing import Any

from ..contracts import CapabilityEvidence
from . import tdx_protocol


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

FINANCE_CODE_FIELDS = frozenset({"province", "industry"})
FINANCE_DATE_FIELDS = frozenset({"updated_date", "ipo_date"})
FINANCE_METADATA_FIELDS = FINANCE_CODE_FIELDS | FINANCE_DATE_FIELDS
FINANCE_SHARE_FIELDS = frozenset({
    "float_shares", "total_shares", "state_shares", "sponsor_legal_shares",
    "legal_shares", "b_shares", "h_shares",
})
FINANCE_PER_SHARE_FIELDS = frozenset({"eps", "net_assets_per_share"})
FINANCE_COUNT_FIELDS = frozenset({"shareholder_count"})
#: No source documents this field: it is returned as sent and carries no unit.
FINANCE_UNDOCUMENTED_FIELDS = frozenset({"reserved2"})

_FINANCE_HEADER_SIZE = 9      # count (H), market (B), code (6s)
_FINANCE_RECORD = "<fHHII" + "f" * 30
_CATEGORY = "<64s80sII"


def build_finance_info_request(market: int, code: str) -> bytes:
    return bytes.fromhex("0c 1f 18 76 00 01 0b 00 0b 00 10 00 01 00") + struct.pack("<B6s", market, tdx_protocol._code(code))


def build_company_categories_request(market: int, code: str) -> bytes:
    return bytes.fromhex("0c 0f 10 9b 00 01 0e 00 0e 00 cf 02") + struct.pack("<H6sI", market, tdx_protocol._code(code), 0)


def build_company_content_request(market: int, code: str, filename: str, start: int, length: int) -> bytes:
    return bytes.fromhex("0c 07 10 9c 00 01 68 00 68 00 d0 02") + struct.pack(
        "<H6sH80sIII", market, tdx_protocol._code(code), 0, filename.encode("ascii"), start, length, 0)


def _finance_unit(name: str) -> str | None:
    if name in FINANCE_SHARE_FIELDS:
        return "股"
    if name in FINANCE_PER_SHARE_FIELDS:
        return "元/股"
    if name in FINANCE_COUNT_FIELDS:
        return "户"
    if name in FINANCE_CODE_FIELDS:
        return "code"
    if name in FINANCE_DATE_FIELDS:
        return "YYYYMMDD"
    if name in FINANCE_UNDOCUMENTED_FIELDS:
        return None
    return "元"


def parse_finance_info(body: bytes) -> dict[str, Any]:
    if len(body) < _FINANCE_HEADER_SIZE + struct.calcsize(_FINANCE_RECORD):
        raise tdx_protocol.TdxProtocolError("short finance response")
    count, market = struct.unpack_from("<HB", body, 0)
    row: dict[str, Any] = {"count": count, "market": market, "code": tdx_protocol.decode_gbk(body[3:9])}
    for name, value in zip(FINANCE_FIELDS, struct.unpack_from(_FINANCE_RECORD, body, _FINANCE_HEADER_SIZE)):
        if name in FINANCE_METADATA_FIELDS:
            row[name] = value
        elif name in FINANCE_SHARE_FIELDS:
            row[name] = float(value) * 10000.0          # raw 万股
        elif name in FINANCE_PER_SHARE_FIELDS or name in FINANCE_COUNT_FIELDS or name in FINANCE_UNDOCUMENTED_FIELDS:
            row[name] = float(value)
        else:
            row[name] = float(value) * 1000.0           # raw 千元
    row["field_units"] = {name: _finance_unit(name) for name in FINANCE_FIELDS}
    row["units"] = {"shares": "股", "amounts": "元", "per_share": "元/股", "eps": "元/股",
                    "finance_raw_money": "千元", "finance_raw_shares": "万股"}
    return row


def parse_company_categories(body: bytes) -> list[dict[str, Any]]:
    if len(body) < 2:
        raise tdx_protocol.TdxProtocolError("short company categories response")
    (count,) = struct.unpack_from("<H", body)
    size = struct.calcsize(_CATEGORY)
    if len(body) < 2 + count * size:
        raise tdx_protocol.TdxProtocolError("truncated company categories response")
    rows = []
    for index in range(count):
        name, filename, start, length = struct.unpack_from(_CATEGORY, body, 2 + index * size)
        rows.append({"name": tdx_protocol.decode_gbk(name), "filename": tdx_protocol.decode_gbk(filename),
                     "start": start, "length": length})
    return rows


def parse_company_content(body: bytes) -> str:
    if len(body) < 12:
        raise tdx_protocol.TdxProtocolError("short company content response")
    (length,) = struct.unpack_from("<H", body, 10)
    if len(body) < 12 + length:
        raise tdx_protocol.TdxProtocolError("truncated company content response")
    return body[12:12 + length].decode("gb18030", "replace")


async def fetch_financial_summary(*, symbol: str) -> CapabilityEvidence:
    """The latest 0x0010 summary. It carries no report period: ``updated_date`` keeps moving after the
    disclosure (delta-1 D4), so it is neither the period nor ``available_at``. The period and the first
    disclosure date come from tipinfo columns 2 and 4 (delta-3 Q1/Q2), joined by the I4 collector."""
    market, code = tdx_protocol.market_code(symbol)
    row, host = await tdx_protocol.call(
        lambda client: parse_finance_info(client._exchange(build_finance_info_request(market, code))),
        handshake_profile="login_one")
    return tdx_protocol.observed_evidence([{
        "symbol": tdx_protocol.symbol(market, code), "report_period": None,
        "statement_items": {name: row[name] for name in FINANCE_FIELDS}, "field_units": row["field_units"]}], host)


async def fetch_company_profile(*, symbol: str) -> CapabilityEvidence:
    market, code = tdx_protocol.market_code(symbol)
    canonical = tdx_protocol.symbol(market, code)

    def read_sections(client: tdx_protocol.TdxClient) -> list[dict[str, Any]]:
        categories = parse_company_categories(client._exchange(build_company_categories_request(market, code)))
        return [{"symbol": canonical, "category": item["name"], "filename": item["filename"],
                 "content": parse_company_content(client._exchange(build_company_content_request(
                     market, code, item["filename"], item["start"], item["length"])))}
                for item in categories]

    rows, host = await tdx_protocol.call(read_sections, handshake_profile="login_one")
    return tdx_protocol.observed_evidence(rows, host)


__all__ = [
    "FINANCE_FIELDS", "build_company_categories_request", "build_company_content_request",
    "build_finance_info_request", "fetch_company_profile", "fetch_financial_summary",
    "parse_company_categories", "parse_company_content", "parse_finance_info",
]
