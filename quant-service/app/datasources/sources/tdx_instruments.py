"""TDX security lists, instrument taxonomy, and index/board bars.

Instrument discovery uses the legacy 0x044e/0x0450 commands on the shared
``tdx_protocol.TdxClient`` with the LOGIN_ONE handshake profile.

Prices in a security-list quote are integer values whose decimal point is a
per-instrument list field.  The quote parser in ``tdx_protocol`` exposes the
legacy /100 value, so :func:`scale_quote` converts all price fields by
``10 ** (2 - decimal_point)``.  This is why convertible bonds and ETFs cannot
share the stock-only /100 rule.
"""

from __future__ import annotations

import dataclasses

import asyncio
import struct
from collections import Counter
from collections.abc import Callable, Sequence
from typing import Any, Iterable, Mapping

from . import tdx_protocol, tdx_files
from ..contracts import CapabilityEvidence
from .tdx_zhb_extras import bj_rows_from_zhb, parse_bj_mapping, parse_tdxbjmore


SECURITY_COUNT = 0x044E
SECURITY_LIST = 0x0450
SECURITY_PAGE_SIZE = 1000
SECURITY_ROW_SIZE = 29
#: The instrument types of A-share stocks and of funds, as instrument_type names them.
STOCK_TYPES = frozenset({"stock_main", "stock_chinext", "stock_star", "stock_bj"})
FUND_TYPES = frozenset({"etf", "lof", "fund"})


def build_security_count_request(market: int) -> bytes:
    if market not in tdx_protocol.MARKETS.values():
        raise ValueError("market must be 0 (SZ), 1 (SH), or 2 (BJ)")
    return (bytes.fromhex("0c 0c 18 6c 00 01 08 00 08 00 4e 04")
            + struct.pack("<H", market) + bytes.fromhex("75 c7 33 01"))


def build_security_list_request(market: int, start: int = 0) -> bytes:
    """One 0x0450 page; the server returns up to SECURITY_PAGE_SIZE rows from ``start``."""
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
        # pre_close is TDX's packed float (pytdx get_volume): 0x418C999A is 17.575, whatever the decimal point.
        row = {"market": market, "code": code, "name": tdx_protocol.decode_gbk(name_raw), "vol_unit": vol_unit,
               "decimal_point": decimal_point, "pre_close": tdx_protocol.decode_volume(pre_close_raw)}
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
            "is_a_share": kind in STOCK_TYPES}


def requested_of_types(symbols: Sequence[str], kinds: frozenset[str]) -> list[tuple[int, str]]:
    """The (market, code) pairs a reader requests for ``symbols``, each of an instrument type in ``kinds``. A symbol of any
    other type is a ValueError, so a reader calls this before it connects."""
    stocks = tdx_protocol.requested_stocks(symbols)
    for item, (market, code) in zip(symbols, stocks):
        kind = instrument_type(market, code)
        if kind not in kinds:
            raise ValueError(f"{item} is a {kind} code; this reader takes only {', '.join(sorted(kinds))}")
    return stocks


def bar_layout(instrument_type: str) -> str:
    """Return the bar record layout for an instrument type.

    Index and board bars have extra up/down-count breadth fields (36 bytes);
    all other instruments use the standard stock layout (32 bytes).
    """
    return "index" if instrument_type in ("index", "board") else "stock"


def _list_section(market: int) -> Callable[[tdx_protocol.TdxClient], tuple[int, list[dict[str, Any]]]]:
    return lambda client: security_list(client, market)


def _zhb_bj_rows(client: tdx_protocol.TdxClient) -> list[dict[str, Any]]:
    return bj_rows_from_zhb(tdx_files.parse_zhb_zip(tdx_files.download(client, "zhb.zip")))


def _security_row(market: int, code: str, name: str, decimal_point: int | None, pre_close: float | None,
                  list_source: str, host: str) -> dict[str, Any]:
    kind = classify_instrument(market, code, name)
    return {"symbol": tdx_protocol.symbol(market, code), "market": market, "code": code, "name": name,
            "instrument_type": kind["type"], "decimal_point": decimal_point, "pre_close": pre_close,
            "is_st": kind["is_st"], "list_source": list_source, "source_host": host}


def _security_list() -> CapabilityEvidence:
    sweep = tdx_protocol.sweep_sync({"SZ": _list_section(0), "SH": _list_section(1),
                                     "BJ": lambda client: security_count(client, 2), "zhb": _zhb_bj_rows},
                                    handshake_profile="login_one")
    host, sections = f"{sweep['host']}/{sweep['profile']}", sweep["sections"]
    lost = [f"{name}: {sections[name]['error']}" for name in ("SZ", "SH", "BJ") if "error" in sections[name]]
    if lost:
        raise tdx_protocol.TdxProtocolError(f"security list sweep on {host} lost a market: " + "; ".join(lost))
    (sz_count, sz_rows), (sh_count, sh_rows) = sections["SZ"]["result"], sections["SH"]["result"]
    bj_count = sections["BJ"]["result"]
    rows = [_security_row(row["market"], row["code"], row["name"], row["decimal_point"], row["pre_close"],
                          "server_list", host) for row in sz_rows + sh_rows]
    warnings = []
    if "error" in sections["zhb"]:
        warnings.append(f"zhb_failed: {sections['zhb']['error']}")
    else:
        # tdxbjmore has no decimal point or pre-close, and its first column is the file's own market number.
        rows += [_security_row(2, row["code"], row["name"], None, None, "zhb_tdxbjmore", host)
                 for row in sections["zhb"]["result"]]
    bj_rows = len(rows) - len(sz_rows) - len(sh_rows)
    if bj_rows != bj_count:
        warnings.append(f"bj_missing={bj_count - bj_rows}")
    return tdx_protocol.observed_evidence(rows, host, coverage=len(rows) / (sz_count + sh_count + bj_count),
                                          warnings=tuple(warnings))


async def fetch_security_list() -> CapabilityEvidence:
    """Every SZ/SH/BJ security from one deterministic host, one connection per section (delta-1 D1, D5).

    Losing the SZ, SH or BJ count section fails the attempt; losing zhb.zip only leaves the BJ rows out,
    which shows in the coverage and the bj_missing warning. Server counts, not returned rows, are the
    denominator, so a missing page lowers the coverage too.
    """
    return await asyncio.to_thread(_security_list)


async def fetch_instruments() -> CapabilityEvidence:
    """The stocks of the security list in the reference.instruments shape; TDX sends no list date."""
    evidence = await fetch_security_list()
    return dataclasses.replace(evidence, rows=[
        {"symbol": row["symbol"], "name": row["name"], "list_date": None, "is_st": row["is_st"]}
        for row in evidence.rows if row["instrument_type"] in STOCK_TYPES])


def price_scale(decimal_point: int | None) -> float:
    """Return the divisor for integer quote prices.

    The list's decimal_point is authoritative.  A security without a valid one (the BJ rows carry none)
    has no divisor: that is a ValueError, never the stock /100.
    """
    if decimal_point is None or not 0 <= decimal_point <= 6:
        raise ValueError(f"quote decimal point must be 0 to 6, got {decimal_point!r}")
    return float(10 ** decimal_point)


_QUOTE_PRICE_KEYS = frozenset({"price", "last_close", "open", "high", "low",
                               *(f"{side}{level}" for side in ("bid", "ask") for level in range(1, 6))})


def scale_quote(quote: Mapping[str, Any], decimal_point: int | None) -> dict[str, Any]:
    factor = 100.0 / price_scale(decimal_point)
    result = dict(quote)
    for key, value in quote.items():
        if key in _QUOTE_PRICE_KEYS:
            if isinstance(value, (int, float)):
                result[key] = value * factor
    result["decimal_point"] = decimal_point
    result["price_divisor"] = price_scale(decimal_point)
    return result


def parse_index_bars(body: bytes, *, category: int = 9) -> list[dict[str, Any]]:
    """Index and board bars (delta-1 1c R2): the stock bar record followed by uint16 up/down counts.

    The counts are per day and count the index's constituents (delta-3 Q10): never mix them with all-A
    breadth. Only this layout is verified; an answer that does not fill it exactly is an error, never
    a guess at another layout (that guess turned 510300 ETF bars into dates like 89161-91-46).
    """
    count = struct.unpack_from("<H", body)[0]
    payload, pos, base, rows = body[2:], 0, 0, []
    for _ in range(count):
        stamp, pos = tdx_protocol._bar_datetime(category, payload, pos)
        open_diff, pos = tdx_protocol.decode_price(payload, pos)
        close_diff, pos = tdx_protocol.decode_price(payload, pos)
        high_diff, pos = tdx_protocol.decode_price(payload, pos)
        low_diff, pos = tdx_protocol.decode_price(payload, pos)
        volume_raw, amount_raw, up, down = struct.unpack_from("<IIHH", payload, pos)
        pos += 12
        opening = open_diff + base
        rows.append({"datetime": stamp, "open": opening / 1000, "close": (opening + close_diff) / 1000,
                     "high": (opening + high_diff) / 1000, "low": (opening + low_diff) / 1000,
                     "volume": tdx_protocol.decode_volume(volume_raw), "amount": tdx_protocol.decode_volume(amount_raw),
                     "up_count": up, "down_count": down})
        base = opening + close_diff
    if pos != len(payload):
        raise tdx_protocol.TdxProtocolError(f"index bar answer has {len(payload) - pos} bytes after {count} records")
    return rows


def security_count(client: tdx_protocol.TdxClient, market: int) -> int:
    return parse_security_count(client._exchange(build_security_count_request(market)))


def security_list(client: tdx_protocol.TdxClient, market: int) -> tuple[int, list[dict[str, Any]]]:
    """The server's 0x044e count for ``market`` and every 0x0450 page of its list (pages are fixed-size)."""
    count = security_count(client, market)
    return count, [row for start in range(0, count, SECURITY_PAGE_SIZE)
                   for row in parse_security_list(client._exchange(build_security_list_request(market, start)), market=market)]


def index_bars(client: tdx_protocol.TdxClient, market: int, code: str, start: int = 0, count: int = 800) -> list[dict[str, Any]]:
    return parse_index_bars(client._exchange(tdx_protocol.build_bars_request(9, market, code, start, count)))


def type_counts(rows: Iterable[Mapping[str, Any]]) -> Counter[str]:
    return Counter(instrument_type(int(row.get("market", 0)), str(row.get("code", "")), str(row.get("name", ""))) for row in rows)


__all__ = ["FUND_TYPES", "SECURITY_COUNT", "SECURITY_LIST", "STOCK_TYPES", "bar_layout", "build_security_count_request",
           "build_security_list_request", "classify_instrument", "fetch_security_list", "fetch_instruments", "instrument_type",
           "parse_bj_mapping", "parse_tdxbjmore", "bj_rows_from_zhb", "parse_index_bars", "parse_security_count", "parse_security_list",
           "security_count", "security_list", "index_bars", "price_scale", "requested_of_types", "scale_quote", "type_counts"]
