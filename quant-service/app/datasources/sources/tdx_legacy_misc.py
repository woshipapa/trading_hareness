"""Read-only adapters for the legacy TDX ranking and index-overview commands."""

from __future__ import annotations

import struct
from typing import Any

from ..contracts import CapabilityEvidence
from . import tdx_protocol
from .tdx_protocol import _code


KMSG_HEARTBEAT = 0x0004
KMSG_PING = 0x0015
KMSG_INDEXMOMENTUM = 0x051C
KMSG_INDEXINFO = 0x051D
KMSG_QUOTESLIST = 0x054B

RANKING_PAGE_SIZE = 80
QUOTE_CATEGORIES = {
    "sh_a": 0,
    "sz_a": 2,
    "all_a": 6,
    "b_shares": 7,
    "star": 8,
    "bj": 12,
    "chinext": 14,
}
QUOTE_SORT_TYPES = {
    "code": 0,
    "name": 1,
    "pre_close": 2,
    "open": 3,
    "high": 4,
    "low": 5,
    "price": 6,
    "bid": 7,
    "ask": 8,
    "volume": 9,
    "amount": 10,
    "last_volume": 11,
    "change": 12,
    "change_pct": 14,
}


def _header(opcode: int, payload: bytes = b"", *, packet_type: int = 0) -> bytes:
    length = len(payload) + 2
    return struct.pack("<BIBHHH", 0x0C, 0, packet_type, length, length, opcode) + payload


def build_quotes_list_request(category: int, sort_type: int, start: int = 0, count: int = RANKING_PAGE_SIZE,
                              sort_reverse: bool = False, mode: int = 5) -> bytes:
    payload = struct.pack("<9H", category, sort_type, start, min(count, RANKING_PAGE_SIZE), int(sort_reverse), mode,
                          0, 1, 0)
    return _header(KMSG_QUOTESLIST, payload)


def _parse_quote_item(data: bytes, pos: int) -> tuple[dict[str, Any], int]:
    market = data[pos]
    code = data[pos + 1:pos + 7].decode("ascii")
    active1 = struct.unpack_from("<H", data, pos + 7)[0]
    pos += 9
    base, pos = tdx_protocol.decode_price(data, pos)
    pre_diff, pos = tdx_protocol.decode_price(data, pos)
    open_diff, pos = tdx_protocol.decode_price(data, pos)
    high_diff, pos = tdx_protocol.decode_price(data, pos)
    low_diff, pos = tdx_protocol.decode_price(data, pos)
    server_time, pos = tdx_protocol.decode_price(data, pos)
    neg_price, pos = tdx_protocol.decode_price(data, pos)
    volume, pos = tdx_protocol.decode_price(data, pos)
    current_volume, pos = tdx_protocol.decode_price(data, pos)
    amount = struct.unpack_from("<f", data, pos)[0]
    pos += 4
    inner, pos = tdx_protocol.decode_price(data, pos)
    outer, pos = tdx_protocol.decode_price(data, pos)
    s_amount, pos = tdx_protocol.decode_price(data, pos)
    open_amount, pos = tdx_protocol.decode_price(data, pos)
    bid, pos = tdx_protocol.decode_price(data, pos)
    ask, pos = tdx_protocol.decode_price(data, pos)
    bid_volume, pos = tdx_protocol.decode_price(data, pos)
    ask_volume, pos = tdx_protocol.decode_price(data, pos)
    unknown, rise_speed, short_turnover = struct.unpack_from("<Hhh", data, pos)
    pos += 6
    min2_amount = struct.unpack_from("<f", data, pos)[0]
    pos += 4
    opening_rush = struct.unpack_from("<h", data, pos)[0]
    pos += 12
    vol_rise_speed, depth = struct.unpack_from("<ff", data, pos)
    pos += 32
    active2 = struct.unpack_from("<H", data, pos)[0]
    pos += 2
    pre_close = (base + pre_diff) / 100
    price = base / 100
    return {
        "market": market,
        "code": code,
        "active1": active1,
        "active2": active2,
        "price": price,
        "pct_change": None if pre_close == 0 else (price / pre_close - 1) * 100,
        "pre_close": pre_close,
        "open": (base + open_diff) / 100,
        "high": (base + high_diff) / 100,
        "low": (base + low_diff) / 100,
        "server_time_raw": server_time,
        "neg_price": neg_price / 100,
        "volume_lots": volume,
        "current_volume_lots": current_volume,
        "amount": amount,
        "inner_volume_lots": inner,
        "outer_volume_lots": outer,
        "s_amount": s_amount,
        "open_amount": open_amount,
        "bid": (base + bid) / 100,
        "ask": (base + ask) / 100,
        "bid_volume_lots": bid_volume,
        "ask_volume_lots": ask_volume,
        "unknown": unknown,
        "rise_speed": rise_speed / 100,
        "short_turnover": short_turnover / 100,
        "min2_amount": min2_amount,
        "opening_rush": opening_rush / 100,
        "vol_rise_speed": vol_rise_speed,
        "depth": depth,
    }, pos


def parse_quotes_list(body: bytes) -> list[dict[str, Any]]:
    if len(body) < 4:
        raise tdx_protocol.TdxProtocolError("truncated 0x054b response header")
    count = struct.unpack_from("<H", body, 2)[0]
    pos = 4
    rows = []
    try:
        for _ in range(count):
            row, pos = _parse_quote_item(body, pos)
            rows.append(row)
    except (IndexError, UnicodeDecodeError, struct.error) as error:
        raise tdx_protocol.TdxProtocolError("truncated 0x054b quote row") from error
    return rows


def build_index_info_request(market: int, code: str) -> bytes:
    return _header(KMSG_INDEXINFO, struct.pack("<H6sI", market, _code(code), 0))


def parse_index_info(body: bytes) -> dict[str, Any]:
    if len(body) < 16:
        raise tdx_protocol.TdxProtocolError("truncated 0x051d response header")
    try:
        order_count = struct.unpack_from("<I", body, 0)[0]
        market = body[4]
        code = body[5:11].decode("ascii")
        active = struct.unpack_from("<H", body, 11)[0]
        pos = 13
        close, pos = tdx_protocol.decode_price(body, pos)
        pre_diff, pos = tdx_protocol.decode_price(body, pos)
        open_diff, pos = tdx_protocol.decode_price(body, pos)
        high_diff, pos = tdx_protocol.decode_price(body, pos)
        low_diff, pos = tdx_protocol.decode_price(body, pos)
        server_time, pos = tdx_protocol.decode_price(body, pos)
        after_hour, pos = tdx_protocol.decode_price(body, pos)
        volume, pos = tdx_protocol.decode_price(body, pos)
        current_volume, pos = tdx_protocol.decode_price(body, pos)
        amount = struct.unpack_from("<f", body, pos)[0]
        pos += 4
        for _ in range(2):
            _, pos = tdx_protocol.decode_price(body, pos)
        open_amount, pos = tdx_protocol.decode_price(body, pos)
        for _ in range(3):
            _, pos = tdx_protocol.decode_price(body, pos)
        up_count, pos = tdx_protocol.decode_price(body, pos)
        down_count, pos = tdx_protocol.decode_price(body, pos)
        for _ in range(10):
            _, pos = tdx_protocol.decode_price(body, pos)
        orders = []
        last_price = 0
        for _ in range(order_count):
            delta, pos = tdx_protocol.decode_price(body, pos)
            unknown, pos = tdx_protocol.decode_price(body, pos)
            order_volume, pos = tdx_protocol.decode_price(body, pos)
            last_price += delta
            orders.append({"price": last_price / 100, "unknown": unknown, "volume_lots": order_volume})
    except (IndexError, UnicodeDecodeError, struct.error) as error:
        raise tdx_protocol.TdxProtocolError("truncated 0x051d response body") from error
    return {
        "order_count": order_count,
        "market": market,
        "code": code,
        "active": active,
        "close": close / 100,
        "pre_close": (close + pre_diff) / 100,
        "open": (close + open_diff) / 100,
        "high": (close + high_diff) / 100,
        "low": (close + low_diff) / 100,
        "server_time_raw": server_time,
        "after_hour": after_hour,
        "volume_lots": volume,
        "current_volume_lots": current_volume,
        "amount": amount,
        "open_amount": open_amount,
        "up_count": up_count,
        "down_count": down_count,
        "orders": orders,
    }


def build_index_momentum_request(market: int, code: str) -> bytes:
    return _header(KMSG_INDEXMOMENTUM, struct.pack("<H6s", market, _code(code)))


def parse_index_momentum(body: bytes) -> list[int]:
    if len(body) < 2:
        raise tdx_protocol.TdxProtocolError("truncated 0x051c response header")
    count = struct.unpack_from("<H", body, 0)[0]
    pos, total = 2, 0
    values = []
    try:
        for _ in range(count):
            delta, pos = tdx_protocol.decode_price(body, pos)
            total += delta
            values.append(total)
    except IndexError as error:
        raise tdx_protocol.TdxProtocolError("truncated 0x051c response body") from error
    return values


def _all_a_snapshot(client: tdx_protocol.TdxClient) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    while True:
        request = build_quotes_list_request(QUOTE_CATEGORIES["all_a"], QUOTE_SORT_TYPES["code"], len(rows))
        page = parse_quotes_list(client._exchange(request))
        rows.extend(page)
        if len(page) < RANKING_PAGE_SIZE:
            return rows


async def fetch_all_a_snapshot() -> CapabilityEvidence:
    rows, host = await tdx_protocol.call(_all_a_snapshot, handshake_profile="login_one")
    return CapabilityEvidence(rows, coverage=1.0, warnings=(f"tdx_host={host}",))


async def fetch_index_overview(symbol: str = "000001.SH") -> CapabilityEvidence:
    market, code = tdx_protocol.market_code(symbol)
    row, host = await tdx_protocol.call(
        lambda client: parse_index_info(client._exchange(build_index_info_request(market, code))),
        handshake_profile="login_one",
    )
    return CapabilityEvidence([row], coverage=1.0, warnings=(f"tdx_host={host}",))


async def fetch_index_momentum(symbol: str = "000001.SH") -> dict[str, Any]:
    market, code = tdx_protocol.market_code(symbol)
    values, host = await tdx_protocol.call(
        lambda client: parse_index_momentum(client._exchange(build_index_momentum_request(market, code))),
        handshake_profile="login_one",
    )
    return {"host": host, "values": values}


async def fetch_ping() -> dict[str, Any]:
    body, host = await tdx_protocol.call(lambda client: client._exchange(_header(KMSG_PING, packet_type=0)),
                                         handshake_profile="login_one")
    return {"host": host, "raw_length": len(body)}


async def fetch_heartbeat() -> dict[str, Any]:
    body, host = await tdx_protocol.call(lambda client: client._exchange(_header(KMSG_HEARTBEAT, packet_type=1)),
                                         handshake_profile="login_one")
    if len(body) < 10:
        raise tdx_protocol.TdxProtocolError("truncated 0x0004 response")
    return {"host": host, "date": struct.unpack_from("<I", body, 6)[0]}


__all__ = [
    "KMSG_HEARTBEAT",
    "KMSG_INDEXINFO",
    "KMSG_INDEXMOMENTUM",
    "KMSG_PING",
    "KMSG_QUOTESLIST",
    "QUOTE_CATEGORIES",
    "QUOTE_SORT_TYPES",
    "RANKING_PAGE_SIZE",
    "build_index_info_request",
    "build_index_momentum_request",
    "build_quotes_list_request",
    "fetch_all_a_snapshot",
    "fetch_heartbeat",
    "fetch_index_momentum",
    "fetch_index_overview",
    "fetch_ping",
    "parse_index_info",
    "parse_index_momentum",
    "parse_quotes_list",
]
