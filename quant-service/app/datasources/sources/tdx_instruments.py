"""TDX security lists, instrument taxonomy, and index/board bars.

This module deliberately subclasses :class:`tdx_protocol.TdxClient` instead of
changing the shared client.  Instrument discovery uses the legacy 0x044e/0x0450
commands and the client sends only ``_SETUP_COMMANDS[0]``: the additional setup
packets put some servers in a restricted mode.

Prices in a security-list quote are integer values whose decimal point is a
per-instrument list field.  The quote parser in ``tdx_protocol`` exposes the
legacy /100 value, so :func:`scale_quote` converts all price fields by
``10 ** (2 - decimal_point)``.  This is why convertible bonds and ETFs cannot
share the stock-only /100 rule.
"""

from __future__ import annotations

import re
import struct
from collections import Counter
from typing import Any, Iterable, Mapping, Sequence

from . import tdx_protocol
from .tdx_zhb_extras import bj_rows_from_zhb, parse_bj_mapping, parse_tdxbjmore


SECURITY_COUNT = 0x044E
SECURITY_LIST = 0x0450
SECURITY_PAGE_SIZE = 1000
SECURITY_ROW_SIZE = 29


def build_security_count_request(market: int) -> bytes:
    if market not in tdx_protocol.MARKETS.values():
        raise ValueError("market must be 0 (SZ), 1 (SH), or 2 (BJ)")
    return (bytes.fromhex("0c 0c 18 6c 00 01 08 00 08 00 4e 04")
            + struct.pack("<H", market) + bytes.fromhex("75 c7 33 01"))


def build_security_list_request(market: int, start: int = 0, count: int = SECURITY_PAGE_SIZE) -> bytes:
    if market not in tdx_protocol.MARKETS.values() or start < 0 or count <= 0 or count > SECURITY_PAGE_SIZE:
        raise ValueError("invalid security-list bounds")
    # The legacy command has a fixed 1000-row page; ``count`` is retained in
    # the API for callers that page the final partial block.
    return (bytes.fromhex("0c 01 18 64 01 01 06 00 06 00")
            + struct.pack("<H", SECURITY_LIST) + struct.pack("<HH", market, start))


def parse_security_count(body: bytes) -> int:
    """Parse a count response, accepting either a bare uint16 or a tiny header."""
    if len(body) < 2:
        raise ValueError("security count response is truncated")
    count = struct.unpack_from("<H", body, 0)[0]
    if count > 100000:
        raise ValueError("security count is implausible")
    return count


def parse_security_list(body: bytes, *, market: int | None = None) -> list[dict[str, Any]]:
    """Decode 29-byte ``<6sH8s4sBI4s`` security rows."""
    if len(body) >= 2 and (len(body) - 2) % SECURITY_ROW_SIZE == 0:
        declared = struct.unpack_from("<H", body, 0)[0]
        pos, limit = 2, len(body)
    elif len(body) % SECURITY_ROW_SIZE == 0:
        declared = len(body) // SECURITY_ROW_SIZE
        pos, limit = 0, len(body)
    else:
        raise ValueError("security-list response is not a whole number of rows")
    rows: list[dict[str, Any]] = []
    for _ in range(min(declared, (limit - pos) // SECURITY_ROW_SIZE)):
        code_raw, vol_unit, name_raw, _reserved, decimal_point, pre_close_raw, _reserved2 = struct.unpack_from(
            "<6sH8s4sBI4s", body, pos)
        pos += SECURITY_ROW_SIZE
        code = code_raw.decode("ascii", "ignore").rstrip("\x00")
        row = {"market": market, "code": code, "name": tdx_protocol.decode_text(name_raw.split(b"\x00", 1)[0]).strip(), "vol_unit": vol_unit,
               "decimal_point": decimal_point, "pre_close_raw": pre_close_raw,
               "pre_close": pre_close_raw / (10 ** decimal_point) if decimal_point < 10 else None}
        rows.append(row)
    return rows


def instrument_type(market: int, code: str, name: str = "") -> str:
    """Classify TDX rows.  Prefixes are intentionally conservative.

    ``stock_main``, ``stock_star``, ``stock_chinext`` and ``stock_bj`` are
    equity classes; ``index`` and ``board`` are non-tradable references;
    ``etf``, ``lof``, ``fund``, ``cb``, ``bond`` and ``b_share`` are products.
    """
    code = str(code).zfill(6)
    upper = name.upper().replace("＊", "*")
    if code.startswith(("880", "881")) and market == 1:
        return "board"
    if (market == 1 and code.startswith(("000", "999"))) or (market == 0 and code.startswith("399")):
        return "index"
    if "ETF" in upper or code.startswith(("510", "511", "512", "513", "515", "516", "518", "560", "561", "588", "159")):
        return "etf"
    if code.startswith("16"):
        return "lof"
    if code.startswith(("110", "111", "113", "118", "123", "127", "128")):
        return "cb"
    if code.startswith(("10", "12", "13")):
        return "bond"
    if (market == 1 and code.startswith("900")) or (market == 0 and code.startswith("200")):
        return "b_share"
    if market == 2 or code.startswith(("43", "83", "87", "92")):
        return "stock_bj"
    if market == 1 and code.startswith(("688", "689")):
        return "stock_star"
    if market == 0 and code.startswith(("300", "301", "302")):
        return "stock_chinext"
    if (market == 1 and code.startswith(("600", "601", "603", "605"))) or (market == 0 and code.startswith(("000", "001", "002", "003"))):
        return "stock_main"
    if code.startswith(("50", "18")):
        return "fund"
    return "other"


def classify_instrument(market: int, code: str, name: str = "") -> dict[str, Any]:
    kind = instrument_type(market, code, name)
    upper = name.upper().replace("＊", "*")
    return {"market": market, "code": code, "name": name, "type": kind,
            "is_st": upper.startswith("ST") or upper.startswith("*ST") or "ST" in upper[:4],
            "is_a_share": kind.startswith("stock_")}


def price_scale(decimal_point: int | None, kind: str | None = None) -> float:
    """Return the divisor for integer quote prices.

    The list's decimal_point is authoritative.  Missing/invalid metadata falls
    back to the ordinary stock /100 convention and is marked by callers.
    """
    if decimal_point is None or not 0 <= int(decimal_point) <= 6:
        return 100.0
    return float(10 ** int(decimal_point))


def scale_quote(quote: Mapping[str, Any], decimal_point: int | None) -> dict[str, Any]:
    factor = 100.0 / price_scale(decimal_point)
    result = dict(quote)
    for key, value in quote.items():
        if key in {"price", "last_close", "open", "high", "low"} or key.startswith(("bid", "ask")):
            if isinstance(value, (int, float)):
                result[key] = value * factor
    result["decimal_point"] = decimal_point
    result["price_divisor"] = price_scale(decimal_point)
    return result


def parse_index_bars(body: bytes, *, category: int = 9) -> list[dict[str, Any]]:
    """Parse index/board bars with the trailing up/down breadth counters.

    The legacy index record is the normal 32-byte bar followed by two uint16
    counters (36 bytes total): date, OHLC integer deltas, volume, amount,
    up_count, down_count.  Some servers omit counters; those records are
    accepted as a fallback and expose ``None`` counters.
    """
    if len(body) < 2:
        return []
    count = struct.unpack_from("<H", body)[0]
    payload = body[2:]
    # This is the pytdx/gotdx wire layout: compressed datetime and four
    # signed price deltas, followed by packed volume/amount and two breadth
    # counters.  It is the same bar stream as 0x0164 with four extra bytes.
    try:
        pos, base = 0, 0
        decoded: list[dict[str, Any]] = []
        for _ in range(count):
            stamp, pos = tdx_protocol._bar_datetime(category, payload, pos)
            open_diff, pos = tdx_protocol.decode_price(payload, pos)
            close_diff, pos = tdx_protocol.decode_price(payload, pos)
            high_diff, pos = tdx_protocol.decode_price(payload, pos)
            low_diff, pos = tdx_protocol.decode_price(payload, pos)
            volume_raw, amount_raw = struct.unpack_from("<II", payload, pos)
            pos += 8
            up, down = struct.unpack_from("<HH", payload, pos)
            pos += 4
            opening = open_diff + base
            decoded.append({"datetime": stamp, "open": opening / 1000, "close": (opening + close_diff) / 1000,
                            "high": (opening + high_diff) / 1000, "low": (opening + low_diff) / 1000,
                            "volume": tdx_protocol.decode_volume(volume_raw), "amount": tdx_protocol.decode_volume(amount_raw),
                            "up_count": up, "down_count": down})
            base = opening + close_diff
        if pos == len(payload):
            return decoded
    except (IndexError, struct.error, ValueError):
        pass
    # A small number of mirrors serve fixed records; retain a bounded fallback
    # for captured fixtures and old file bridges.
    # ``GetIndexBars`` is commonly fixed-float (36 bytes); a few mirrors use
    # compact integer OHLC (also 36 bytes including the breadth counters).
    record_size = 36 if count and len(payload) >= count * 36 else 32
    float_layout = False
    if record_size == 36:
        try:
            sample = struct.unpack_from("<IfffffIIHH", payload)
            float_layout = all(abs(value) < 1e9 for value in sample[1:6]) and sample[8] < 100000 and sample[9] < 100000
        except struct.error:
            pass
    rows: list[dict[str, Any]] = []
    pos, base = 0, 0
    for _ in range(count):
        if pos + record_size > len(payload):
            break
        if float_layout:
            packed, opening, high, low, closing, amount, volume, _reserved, up, down = struct.unpack_from("<IfffffIIHH", payload, pos)
            pos += 36
            rows.append({"datetime": f"{packed // 10000:04d}-{packed % 10000 // 100:02d}-{packed % 100:02d}",
                         "open": opening, "close": closing, "high": high, "low": low,
                         "volume": volume, "amount": amount, "up_count": up, "down_count": down})
            continue
        packed, open_diff, close_diff, high_diff, low_diff, volume, amount, _reserved = struct.unpack_from("<IiiiiIII", payload, pos)
        pos += 32
        up = down = None
        if record_size == 36:
            up, down = struct.unpack_from("<HH", payload, pos)
            pos += 4
        stamp = f"{packed // 10000:04d}-{packed % 10000 // 100:02d}-{packed % 100:02d}"
        opening = open_diff + base
        rows.append({"datetime": stamp, "open": opening / 1000, "close": (opening + close_diff) / 1000,
                     "high": (opening + high_diff) / 1000, "low": (opening + low_diff) / 1000,
                     "volume": volume, "amount": amount, "up_count": up, "down_count": down})
        base = opening + close_diff
    return rows


def normalize_bj_symbol(old: str, mapping: Mapping[str, str] | None = None) -> str:
    code = str(old).upper().replace(".BJ", "")
    if not re.fullmatch(r"\d{6}", code):
        raise ValueError("BJ symbol must be six digits")
    new = (mapping or {}).get(code, code)
    return f"{new}.BJ"


def security_count(client: tdx_protocol.TdxClient, market: int) -> int:
    return parse_security_count(client._exchange(build_security_count_request(market)))


def security_list(client: tdx_protocol.TdxClient, market: int, *, page_size: int = SECURITY_PAGE_SIZE) -> list[dict[str, Any]]:
    total = security_count(client, market)
    rows: list[dict[str, Any]] = []
    for start in range(0, total, page_size):
        body = client._exchange(build_security_list_request(market, start, min(page_size, total - start)))
        rows.extend(parse_security_list(body, market=market))
    return rows


def index_bars(client: tdx_protocol.TdxClient, market: int, code: str, start: int = 0, count: int = 800) -> list[dict[str, Any]]:
    return parse_index_bars(client._exchange(tdx_protocol.build_bars_request(9, market, code, start, count)))


def type_counts(rows: Iterable[Mapping[str, Any]]) -> Counter[str]:
    return Counter(instrument_type(int(row.get("market", 0)), str(row.get("code", "")), str(row.get("name", ""))) for row in rows)


__all__ = ["SECURITY_COUNT", "SECURITY_LIST", "build_security_count_request",
           "build_security_list_request", "classify_instrument", "instrument_type", "normalize_bj_symbol",
           "parse_bj_mapping", "parse_tdxbjmore", "bj_rows_from_zhb", "parse_index_bars", "parse_security_count", "parse_security_list",
           "security_count", "security_list", "index_bars", "price_scale", "scale_quote", "type_counts"]
