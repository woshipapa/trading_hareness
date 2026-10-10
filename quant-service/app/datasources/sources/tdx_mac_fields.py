"""MAC dynamic-field registry and small, read-only protocol helpers.

The MAC service returns one little-endian four-byte value for every set bit,
in bit order.  Unknown bits intentionally retain their wire identity
(``bit_0xNN``); they must not become business fields without a reference.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
import struct
from typing import Any, Iterable

from . import tdx_protocol
from .tdx_mac import TdxMacError, _fixed, _float, build_request

MAC_FIELD_BYTES = 20
MAC_FIELD_BITS = MAC_FIELD_BYTES * 8
MATCH = "MATCH"
MISMATCH = "MISMATCH"
NO_REFERENCE = "NO_REFERENCE"


@dataclass(frozen=True)
class MACField:
    bit: int
    name: str
    format: str
    unit: str
    canonical_key: str | None
    confidence: str
    reconciliation: str = NO_REFERENCE
    capability_ids: tuple[str, ...] = ()
    note: str = ""


def _field(
    bit: int,
    name: str,
    fmt: str = "float32",
    unit: str = "",
    canonical: str | None = None,
    confidence: str = "medium",
    reconciliation: str = NO_REFERENCE,
    capabilities: Iterable[str] = (),
    note: str = "",
) -> MACField:
    return MACField(
        bit, name, fmt, unit, canonical, confidence, reconciliation, tuple(capabilities), note
    )


_MAIN_FLOW_NOTE = "provider-defined main_in - main_out, yuan"
_SAMPLED_NOTE = (
    "sampled intraday change: equals a 1-minute bar at or next to the named time, "
    "not necessarily the same slot"
)

# Names and formats are transcribed from gotdx's mac_board_members_dynamic.go.
# Sparse bits are retained below as wire names, which makes full-bit probes safe.
# Each entry is (name, format, unit, canonical key, confidence, reconciliation, capabilities[, note]); a
# canonical key of None stays None, so a field no source documents is never offered under a business key.


_BASIC = {
    0: (
        "pre_close",
        "float32",
        "yuan",
        "pre_close",
        "high",
        MATCH,
        ("quote.watch_snapshot",),
    ),
    1: (
        "open",
        "float32",
        "yuan",
        "open",
        "high",
        MATCH,
        ("quote.watch_snapshot", "auction.open_snapshot"),
    ),
    2: ("high", "float32", "yuan", "high", "high", MATCH, ("quote.watch_snapshot",)),
    3: ("low", "float32", "yuan", "low", "high", MATCH, ("quote.watch_snapshot",)),
    4: ("close", "float32", "yuan", "close", "high", MATCH, ("quote.watch_snapshot",)),
    5: (
        "vol",
        "uint32",
        "lots",
        "volume_lots",
        "high",
        MATCH,
        ("quote.watch_snapshot",),
    ),
    6: ("vol_ratio", "float32", "ratio", "vol_ratio", "medium", NO_REFERENCE, ()),
    7: (
        "amount",
        "float32",
        "yuan",
        "amount_yuan",
        "high",
        MATCH,
        ("quote.watch_snapshot", "flow.stock_daily"),
    ),
    8: (
        "inside_volume",
        "uint32",
        "shares",
        "inside_volume",
        "medium",
        NO_REFERENCE,
        (),
    ),
    9: (
        "outside_volume",
        "uint32",
        "shares",
        "outside_volume",
        "medium",
        NO_REFERENCE,
        (),
    ),
    10: (
        "total_shares",
        "float32",
        "10k_shares",
        "total_shares",
        "medium",
        NO_REFERENCE,
        (),
    ),
    11: (
        "float_shares",
        "float32",
        "10k_shares",
        "float_shares",
        "medium",
        NO_REFERENCE,
        (),
    ),
    12: ("eps", "float32", "yuan", "eps", "medium", NO_REFERENCE, ()),
    13: ("net_assets", "float32", "yuan", "net_assets", "medium", NO_REFERENCE, ()),
    14: ("security_type_price", "float32", "yuan", None, "low", NO_REFERENCE, ()),
    15: (
        "total_market_cap_ab",
        "float32",
        "yuan",
        "market_cap",
        "medium",
        NO_REFERENCE,
        (),
    ),
    16: ("pe_dynamic", "float32", "ratio", "pe_dynamic", "medium", NO_REFERENCE, ()),
    17: ("bid_price", "float32", "yuan", "bid_price", "high", NO_REFERENCE, ()),
    18: ("ask_price", "float32", "yuan", "ask_price", "high", NO_REFERENCE, ()),
    19: (
        "server_update_date",
        "uint32",
        "YYYYMMDD",
        "server_update_date",
        "high",
        NO_REFERENCE,
        (),
    ),
    20: (
        "server_update_time",
        "uint32",
        "HHMMSS",
        "server_update_time",
        "high",
        NO_REFERENCE,
        (),
    ),
    21: ("lot_size_info", "uint32", "", None, "low", NO_REFERENCE, ()),
    22: (
        "bit_0x16",
        "int32",
        "",
        None,
        "unknown",
        NO_REFERENCE,
        (),
    ),
    23: (
        "dividend_yield",
        "float32",
        "yuan",
        "dividend_yield",
        "medium",
        NO_REFERENCE,
        (),
    ),
    24: ("bid_volume", "uint32", "shares", "bid_volume", "high", NO_REFERENCE, ()),
    25: ("ask_volume", "uint32", "shares", "ask_volume", "high", NO_REFERENCE, ()),
    26: ("last_volume", "uint32", "shares", "last_volume", "high", NO_REFERENCE, ()),
    27: (
        "turnover",
        "float32",
        "percent",
        "turnover",
        "medium",
        MATCH,
        ("quote.watch_snapshot",),
    ),
    28: ("industry", "uint32", "code", "industry", "medium", NO_REFERENCE, ()),
    29: (
        "bit_0x1d",
        "float32",
        "",
        None,
        "unknown",
        NO_REFERENCE,
        (),
    ),
    30: (
        "stock_tag_flags",
        "uint32",
        "bitset",
        "stock_tag_flags",
        "medium",
        NO_REFERENCE,
        (),
    ),
    31: (
        "decimal_point",
        "uint32",
        "digits",
        "decimal_point",
        "high",
        NO_REFERENCE,
        (),
    ),
    32: (
        "buy_price_limit",
        "float32",
        "yuan",
        "limit_up",
        "high",
        MATCH,
        ("limits.ladder", "limits.stock_anomaly_reason", "limits.prices"),
    ),
    33: (
        "sell_price_limit",
        "float32",
        "yuan",
        "limit_down",
        "high",
        MATCH,
        ("limits.ladder", "limits.stock_anomaly_reason", "limits.prices"),
    ),
    34: (
        "price_decimal_info",
        "uint32",
        "bitset",
        "price_decimal_info",
        "low",
        NO_REFERENCE,
        (),
    ),
    35: ("lot_size", "uint32", "shares", "lot_size", "medium", NO_REFERENCE, ()),
    36: ("pre_iopv", "float32", "yuan", "pre_iopv", "low", NO_REFERENCE, ()),
    37: ("speed_pct", "float32", "percent", "speed_pct", "medium", NO_REFERENCE, ()),
    38: (
        "avg_price",
        "float32",
        "yuan",
        "avg_price",
        "high",
        MATCH,
        ("quote.watch_snapshot",),
    ),
    39: ("iopv", "float32", "yuan", "iopv", "medium", MATCH, ("fund.iopv",)),
    40: ("pe_ttm_vol_related", "float32", "ratio", None, "low", NO_REFERENCE, ()),
    41: ("ex_price_placeholder", "float32", "yuan", None, "low", NO_REFERENCE, ()),
    42: (
        "operating_revenue",
        "float32",
        "10k_yuan",
        "operating_revenue",
        "medium",
        NO_REFERENCE,
        (),
    ),
    43: ("flag_kcb", "uint32", "flag", "flag_kcb", "high", NO_REFERENCE, ()),
    44: ("flag_bj", "uint32", "flag", "flag_bj", "high", NO_REFERENCE, ()),
    45: (
        "circulating_capital_z",
        "float32",
        "10k_shares",
        "circulating_capital",
        "low",
        NO_REFERENCE,
        (),
    ),
    46: (
        "after_hours_volume",
        "int32",
        "shares",
        "after_hours_volume",
        "medium",
        NO_REFERENCE,
        (),
    ),
    48: ("pe_ttm", "float32", "ratio", "pe_ttm", "medium", NO_REFERENCE, ()),
    49: ("pe_static", "float32", "ratio", "pe_static", "medium", NO_REFERENCE, ()),
    55: ("index_metric", "float32", "", "index_metric", "low", NO_REFERENCE, ()),
    56: (
        "main_net_amount",
        "float32",
        "yuan",
        "main_net_amount",
        "medium",
        NO_REFERENCE,
        ("flow.stock_daily",),
        _MAIN_FLOW_NOTE,
    ),
    57: (
        "bid_ask_ratio",
        "float32",
        "percent",
        "bid_ask_ratio",
        "medium",
        NO_REFERENCE,
        (),
    ),
    58: ("non_index_flag", "uint32", "flag", "non_index_flag", "low", NO_REFERENCE, ()),
    60: (
        "ytd_pct",
        "float32",
        "percent",
        "ytd_pct",
        "medium",
        MATCH,
        ("quote.watch_snapshot",),
    ),
    64: (
        "mtd_pct",
        "float32",
        "percent",
        "mtd_pct",
        "high",
        MATCH,
        ("quote.watch_snapshot",),
    ),
    65: (
        "change_1y_pct",
        "float32",
        "percent",
        "change_1y_pct",
        "high",
        MATCH,
        ("quote.watch_snapshot",),
    ),
    66: (
        "prev_change_pct",
        "float32",
        "percent",
        "prev_change_pct",
        "medium",
        NO_REFERENCE,
        (),
    ),
    67: (
        "change_3d_pct",
        "float32",
        "percent",
        "change_3d_pct",
        "high",
        MATCH,
        ("quote.watch_snapshot",),
    ),
    68: (
        "change_60d_pct",
        "float32",
        "percent",
        "change_60d_pct",
        "high",
        MATCH,
        ("quote.watch_snapshot",),
    ),
    69: (
        "change_5d_pct",
        "float32",
        "percent",
        "change_5d_pct",
        "high",
        MATCH,
        ("quote.watch_snapshot",),
    ),
    70: (
        "change_10d_pct",
        "float32",
        "percent",
        "change_10d_pct",
        "high",
        MATCH,
        ("quote.watch_snapshot",),
    ),
    71: (
        "prev2_change_pct",
        "float32",
        "percent",
        "prev2_change_pct",
        "medium",
        NO_REFERENCE,
        (),
    ),
    72: ("bid2_price", "float32", "yuan", "bid2_price", "high", NO_REFERENCE, ()),
    73: ("ask2_price", "float32", "yuan", "ask2_price", "high", NO_REFERENCE, ()),
    74: ("ah_code", "uint32", "code", "ah_code", "medium", NO_REFERENCE, ()),
    75: ("bit_0x4b", "uint32", "code", None, "unverified", NO_REFERENCE, ()),
    87: (
        "open_amount",
        "float32",
        "yuan",
        "open_amount",
        "medium",
        MATCH,
        ("auction.open_snapshot",),
    ),
    88: (
        "annual_limit_up_days",
        "int32",
        "days",
        "annual_limit_up_days",
        "medium",
        MATCH,
        (),
        "limit-up days in the calendar year, not a rolling window",
    ),
    89: ("bit_0x59", "uint32", "", None, "unknown", NO_REFERENCE, ()),
    91: (
        "dividend_yield_rate",
        "float32",
        "percent",
        "dividend_yield_rate",
        "medium",
        NO_REFERENCE,
        (),
    ),
    92: (
        "close_streak",
        "int32",
        "days",
        "close_streak",
        "high",
        MATCH,
        ("quote.watch_snapshot",),
    ),
    93: ("bit_0x5d", "uint32", "", None, "unknown", NO_REFERENCE, ()),
    94: ("bit_0x5e", "uint32", "", None, "unknown", NO_REFERENCE, ()),
    95: ("industry_sub", "uint32", "code", "industry_sub", "medium", NO_REFERENCE, ()),
    102: (
        "auction_buy_limit",
        "float32",
        "yuan",
        "auction_buy_limit",
        "medium",
        MATCH,
        ("auction.open_snapshot",),
    ),
    103: (
        "auction_sell_limit",
        "float32",
        "yuan",
        "auction_sell_limit",
        "medium",
        MATCH,
        ("auction.open_snapshot",),
    ),
    104: (
        "vol_speed_pct",
        "float32",
        "percent",
        "vol_speed_pct",
        "medium",
        NO_REFERENCE,
        (),
    ),
    105: (
        "short_turnover_pct",
        "float32",
        "percent",
        "short_turnover_pct",
        "medium",
        NO_REFERENCE,
        (),
    ),
    106: ("amount_2m", "float32", "yuan", "amount_2m", "medium", NO_REFERENCE, ()),
    107: (
        "main_net_amount_copy",
        "float32",
        "yuan",
        "main_net_amount",
        "medium",
        NO_REFERENCE,
        ("flow.stock_daily",),
        _MAIN_FLOW_NOTE,
    ),
    115: (
        "ddx",
        "float32",
        "ratio",
        "ddx",
        "medium",
        NO_REFERENCE,
        ("flow.stock_daily",),
    ),
    119: ("stock_flag_a", "float32", "flag", "stock_flag_a", "low", NO_REFERENCE, ()),
    120: ("stock_flag_b", "float32", "flag", "stock_flag_b", "low", NO_REFERENCE, ()),
    122: (
        "bit_0x7a",
        "float32",
        "",
        None,
        "unknown",
        NO_REFERENCE,
        (),
    ),
    123: ("prev_amount", "float32", "yuan", "prev_amount", "medium", NO_REFERENCE, ()),
    125: (
        "recent_indicator",
        "float32",
        "",
        "recent_indicator",
        "low",
        NO_REFERENCE,
        (),
    ),
    128: ("bid3_price", "float32", "yuan", "bid3_price", "high", NO_REFERENCE, ()),
    129: ("bid4_price", "float32", "yuan", "bid4_price", "high", NO_REFERENCE, ()),
    130: ("bid5_price", "float32", "yuan", "bid5_price", "high", NO_REFERENCE, ()),
    131: ("ask3_price", "float32", "yuan", "ask3_price", "high", NO_REFERENCE, ()),
    132: ("ask4_price", "float32", "yuan", "ask4_price", "high", NO_REFERENCE, ()),
    133: ("ask5_price", "float32", "yuan", "ask5_price", "high", NO_REFERENCE, ()),
    134: ("bid3_volume", "uint32", "shares", "bid3_volume", "high", NO_REFERENCE, ()),
    135: ("bid4_volume", "uint32", "shares", "bid4_volume", "high", NO_REFERENCE, ()),
    136: (
        "up_count",
        "uint32",
        "count",
        "up_count",
        "high",
        MATCH,
        ("sector.index_quote",),
    ),
    137: ("ask3_volume", "uint32", "shares", "ask3_volume", "high", NO_REFERENCE, ()),
    138: ("ask4_volume", "uint32", "shares", "ask4_volume", "high", NO_REFERENCE, ()),
    139: (
        "down_count",
        "uint32",
        "count",
        "down_count",
        "high",
        MATCH,
        ("sector.index_quote",),
    ),
    140: ("bid_ask_diff", "int32", "shares", "bid_ask_diff", "high", NO_REFERENCE, ()),
    141: (
        "change_up_type",
        "int32",
        "flag",
        "change_up_type",
        "medium",
        NO_REFERENCE,
        (),
    ),
    142: ("safety_score", "float32", "score", "safety_score", "low", NO_REFERENCE, ()),
    143: (
        "highlight_count",
        "float32",
        "count",
        "highlight_count",
        "low",
        NO_REFERENCE,
        (),
    ),
    144: (
        "change_at_1000",
        "float32",
        "percent",
        "change_at_1000",
        "medium",
        MATCH,
        (),
        _SAMPLED_NOTE,
    ),
    145: (
        "change_at_1030",
        "float32",
        "percent",
        "change_at_1030",
        "medium",
        MATCH,
        (),
        _SAMPLED_NOTE,
    ),
    146: (
        "change_at_1100",
        "float32",
        "percent",
        "change_at_1100",
        "medium",
        MATCH,
        (),
        _SAMPLED_NOTE,
    ),
    147: (
        "change_at_1130",
        "float32",
        "percent",
        "change_at_1130",
        "medium",
        MATCH,
        (),
        _SAMPLED_NOTE,
    ),
    148: (
        "change_at_1330",
        "float32",
        "percent",
        "change_at_1330",
        "medium",
        MATCH,
        (),
        _SAMPLED_NOTE,
    ),
    149: (
        "change_at_1400",
        "float32",
        "percent",
        "change_at_1400",
        "medium",
        MATCH,
        (),
        _SAMPLED_NOTE,
    ),
    150: (
        "change_at_1430",
        "float32",
        "percent",
        "change_at_1430",
        "medium",
        MATCH,
        (),
        _SAMPLED_NOTE,
    ),
}
# delta-3 Q4 closed the flow windows 0x6c-0x72 and the DD indicators 0x74-0x76 as UNKNOWN, like the Q5 bits above:
# wire name, no unit, no canonical key, no capability.
_BASIC.update({
    bit: (f"bit_0x{bit:02x}", "float32", "", None, "unknown", NO_REFERENCE, ())
    for bit in (*range(0x6C, 0x73), 0x74, 0x75, 0x76)
})
_KNOWN: dict[int, MACField] = {bit: _field(bit, *args) for bit, args in _BASIC.items()}

# Preserve the upstream wire format even when the semantic name is unknown.
_UNKNOWN_FORMATS = {
    47: "float32",
    50: "uint32",
    51: "uint32",
    52: "uint32",
    53: "float32",
    54: "float32",
    61: "float32",
    62: "uint32",
    63: "uint32",
    **{bit: "float32" for bit in range(76, 87)},
}
MAC_FIELDS: tuple[MACField, ...] = tuple(
    _KNOWN.get(
        bit,
        _field(
            bit,
            f"bit_0x{bit:02x}",
            _UNKNOWN_FORMATS.get(bit, "uint32"),
            "",
            None,
            "unverified",
        ),
    )
    for bit in range(MAC_FIELD_BITS)
)
FIELD_BY_BIT = {field.bit: field for field in MAC_FIELDS}


def bitmap_for_bits(bits: Iterable[int]) -> bytes:
    bitmap = bytearray(MAC_FIELD_BYTES)
    for bit in bits:
        if 0 <= bit < MAC_FIELD_BITS:
            bitmap[bit // 8] |= 1 << (bit % 8)
    return bytes(bitmap)


def active_fields(bitmap: bytes) -> tuple[MACField, ...]:
    if len(bitmap) != MAC_FIELD_BYTES:
        raise ValueError("MAC bitmap must be exactly 20 bytes")
    return tuple(
        FIELD_BY_BIT[bit]
        for bit in range(MAC_FIELD_BITS)
        if bitmap[bit // 8] & (1 << (bit % 8))
    )


def decode_dynamic_row(
    data: bytes, bitmap: bytes, offset: int = 0
) -> tuple[dict[str, Any], int]:
    fields = active_fields(bitmap)
    end = offset + 4 * len(fields)
    if end > len(data):
        raise ValueError(f"truncated MAC dynamic row: need {4 * len(fields)} bytes")
    values: dict[str, Any] = {}
    for index, field in enumerate(fields):
        raw = data[offset + index * 4 : offset + index * 4 + 4]
        if field.format == "float32":
            value: Any = struct.unpack("<f", raw)[0]
        elif field.format == "int32":
            value = struct.unpack("<i", raw)[0]
        else:
            value = struct.unpack("<I", raw)[0]
        values[field.name] = value
    return values, end


def decode_dynamic_response(body: bytes) -> list[dict[str, Any]]:
    """Decode the common 20-byte bitmap/total/count response envelope."""
    if len(body) < 26:
        raise TdxMacError(f"truncated response envelope: need 26 bytes, got {len(body)}")
    bitmap, total, count = (
        body[:20],
        struct.unpack_from("<I", body, 20)[0],
        struct.unpack_from("<H", body, 24)[0],
    )
    row_size = 68 + 4 * len(active_fields(bitmap))
    rows: list[dict[str, Any]] = []
    expected_size = 26 + count * row_size
    if len(body) < expected_size:
        raise TdxMacError(f"truncated response: need {expected_size} bytes, got {len(body)}")
    if len(body) > expected_size:
        raise TdxMacError(f"trailing bytes in response: got {len(body)}, expected {expected_size}")
    for index in range(count):
        start = 26 + index * row_size
        row = {
            "market": struct.unpack_from("<H", body, start)[0],
            "symbol": tdx_protocol.decode_gbk(body[start + 2 : start + 24]),
            "name": tdx_protocol.decode_gbk(body[start + 24 : start + 68]),
            "total": total,
        }
        values, _ = decode_dynamic_row(body, bitmap, start + 68)
        row.update(values)
        rows.append(row)
    return rows


def build_symbol_info_request(symbol: str) -> bytes:
    market, code = tdx_protocol.market_code(symbol)
    return build_request(
        0x122A, struct.pack("<H22sI12s", market, _fixed(code, 22), 1, b"")
    )


def build_transactions_request(
    symbol: str, query_date: int, start: int = 0, count: int = 1000
) -> bytes:
    market, code = tdx_protocol.market_code(symbol)
    return build_request(
        0x122F,
        struct.pack(
            "<H22sI I H10s", market, _fixed(code, 22), query_date, start, count, b""
        ),
    )


def build_server_info_request() -> bytes:
    payload = bytearray(68)
    payload[:4] = b"\x04\x00\x2d\x31"
    payload[12:16] = b"\x00\x27\x06\x0e"
    return build_request(0x120F, bytes(payload))


def build_capital_flow_request(symbol: str) -> bytes:
    """The capital-flow query: 0x1218 with head=2 and ``Stock_ZJLX``.  The belong-board query is the same opcode
    with head=1 and ``Stock_GLHQ``; ``tdx_mac.build_aux_request`` builds that one."""
    market, code = tdx_protocol.market_code(symbol)
    return build_request(
        0x1218,
        struct.pack("<H8s16s21s", market, _fixed(code, 8), b"", _fixed("Stock_ZJLX", 21)),
        head=2,
    )


def parse_json_rows(body: bytes, prefix: int = 27) -> list[list[Any]]:
    if len(body) <= prefix:
        return []
    try:
        value = json.loads(body[prefix:].decode("gbk", "replace"))
    except (ValueError, UnicodeError):
        return []
    return value if isinstance(value, list) else []


def parse_symbol_info(body: bytes) -> dict[str, Any]:
    if len(body) < 194:
        return {}
    return {
        "market": struct.unpack_from("<H", body, 8)[0],
        "code": tdx_protocol.decode_gbk(body[10:32]),
        "name": tdx_protocol.decode_gbk(body[32:76]),
        "date": struct.unpack_from("<I", body, 96)[0],
        "time": struct.unpack_from("<I", body, 100)[0],
        "activity": struct.unpack_from("<I", body, 104)[0],
        "pre_close": _float(body, 108),
        "open": _float(body, 112),
        "high": _float(body, 116),
        "low": _float(body, 120),
        "close": _float(body, 124),
        "momentum": _float(body, 128),
        "vol": struct.unpack_from("<I", body, 132)[0],
        "amount": _float(body, 136),
        "inside_volume": struct.unpack_from("<I", body, 140)[0],
        "outside_volume": struct.unpack_from("<I", body, 144)[0],
        "decimal": struct.unpack_from("<H", body, 148)[0],
        "vr": _float(body, 182),
        "turnover": _float(body, 186),
        "avg": _float(body, 190),
    }


def parse_transactions(body: bytes) -> list[dict[str, Any]]:
    if len(body) < 39:
        return []
    count = struct.unpack_from("<H", body, 29)[0]
    rows = []
    for index in range(count):
        pos = 39 + index * 18
        if pos + 18 > len(body):
            break
        rows.append(
            {
                "seconds": struct.unpack_from("<I", body, pos)[0],
                "price": _float(body, pos + 4),
                "vol": struct.unpack_from("<I", body, pos + 8)[0],
                "trade_count": struct.unpack_from("<I", body, pos + 12)[0],
                "buy_or_sell": struct.unpack_from("<H", body, pos + 16)[0],
            }
        )
    return rows


def parse_server_info(body: bytes) -> dict[str, Any]:
    if len(body) < 87:
        return {}

    def sessions(offset: int) -> list[tuple[int, int]]:
        return [
            struct.unpack_from("<HH", body, offset + index * 4) for index in range(4)
        ]

    return {
        "count": struct.unpack_from("<H", body, 0)[0],
        "today": struct.unpack_from("<I", body, 22)[0],
        "sessions1": sessions(30),
        "sessions2": sessions(46),
        "last_trading_day": struct.unpack_from("<I", body, 63)[0],
        "last_trading_day2": struct.unpack_from("<I", body, 71)[0],
        "market_param1": struct.unpack_from("<I", body, 79)[0],
        "market_param2": struct.unpack_from("<I", body, 83)[0],
    }


def parse_auction(body: bytes) -> list[dict[str, Any]]:
    if len(body) < 36:
        return []
    count = struct.unpack_from("<I", body, 24)[0]
    rows = []
    for index in range(count):
        pos = 36 + index * 16
        if pos + 16 > len(body):
            break
        rows.append(
            {
                "seconds": struct.unpack_from("<I", body, pos)[0],
                "price": _float(body, pos + 4),
                "matched": struct.unpack_from("<I", body, pos + 8)[0],
                "unmatched": struct.unpack_from("<i", body, pos + 12)[0],
            }
        )
    return rows


def parse_tick_charts_header(body: bytes) -> dict[str, Any]:
    if len(body) < 71:
        return {}
    return {
        "market": struct.unpack_from("<H", body, 0)[0],
        "code": tdx_protocol.decode_gbk(body[2:24]),
        "dates": [
            struct.unpack_from("<I", body, 24 + index * 4)[0] for index in range(5)
        ],
        "pre_closes": [_float(body, 44 + index * 4) for index in range(5)],
        "count": struct.unpack_from("<H", body, 64)[0],
        "total": struct.unpack_from("<H", body, 69)[0],
    }


def parse_market_monitor(body: bytes) -> list[dict[str, Any]]:
    if len(body) < 2:
        return []
    count = struct.unpack_from("<H", body, 0)[0]
    return [
        {
            "market": struct.unpack_from("<H", body, 2 + index * 32)[0],
            "code": tdx_protocol.decode_gbk(body[4 + index * 32 : 10 + index * 32]),
            "unusual_type": body[11 + index * 32],
        }
        for index in range(count)
        if 34 + index * 32 <= len(body)
    ]


def reconcile(
    actual: float | int | None, expected: float | int | None, tolerance: float = 1e-4
) -> str:
    if (
        actual is None
        or expected is None
        or not math.isfinite(float(actual))
        or not math.isfinite(float(expected))
    ):
        return NO_REFERENCE
    return (
        MATCH
        if math.isclose(
            float(actual), float(expected), rel_tol=tolerance, abs_tol=tolerance
        )
        else MISMATCH
    )


__all__ = [
    "MACField",
    "MAC_FIELDS",
    "FIELD_BY_BIT",
    "MATCH",
    "MISMATCH",
    "NO_REFERENCE",
    "active_fields",
    "bitmap_for_bits",
    "decode_dynamic_row",
    "decode_dynamic_response",
    "reconcile",
    "build_symbol_info_request",
    "build_transactions_request",
    "build_server_info_request",
    "build_capital_flow_request",
    "parse_json_rows",
    "parse_symbol_info",
    "parse_transactions",
    "parse_server_info",
    "parse_auction",
    "parse_tick_charts_header",
    "parse_market_monitor",
]
