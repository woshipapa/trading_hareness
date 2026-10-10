"""Legacy TDX microstructure commands.

The command layouts are the public ``gotdx``/``zytdx`` layouts.  This module
keeps the wire builders and parsers pure and uses :class:`TdxClient` only for
the existing setup, framing, timeout and decompression behavior.
"""

from __future__ import annotations

import struct
from datetime import date
from typing import Any

from ..contracts import CapabilityEvidence
from . import tdx_protocol


VOLUME_PROFILE = 0x051A
MINUTE_TIME_DATA = 0x0537
HISTORY_MINUTE_TIME_DATA = 0x0FEB
AUCTION = 0x056A
TOP_BOARD = 0x053F
UNUSUAL = 0x0563
MINUTE_SERIES = 0x0FB4

_REQUEST_SEQUENCE = 0x01010817
_REQUEST_PACKET_TYPE = 1


def _date_number(value: date | str | int) -> int:
    if isinstance(value, date):
        return int(value.strftime("%Y%m%d"))
    if isinstance(value, str):
        return int(value.replace("-", ""))
    return int(value)


def _request(opcode: int, payload: bytes) -> bytes:
    length = len(payload) + 2
    return struct.pack("<BIBHHH", 0x0C, _REQUEST_SEQUENCE, _REQUEST_PACKET_TYPE,
                       length, length, opcode) + payload


def _unit(code: str) -> float:
    return 1000.0 if code[:2] in {"15", "51", "56", "58"} else 100.0


def _profile_delta(value: int) -> int:
    """Normalize profile deltas sent as unsigned 32-bit two's complement."""
    if value >= 1 << 31:
        return struct.unpack("<i", struct.pack("<I", value & 0xFFFFFFFF))[0]
    return value


def build_volume_profile_request(market: int, code: str) -> bytes:
    return _request(VOLUME_PROFILE, struct.pack("<H6s", market, tdx_protocol._code(code)))


def parse_volume_profile(body: bytes) -> dict[str, Any]:
    if len(body) < 13:
        raise tdx_protocol.TdxProtocolError("short volume profile response")
    count, market = struct.unpack_from("<HB", body, 0)
    code = tdx_protocol.decode_gbk(body[3:9])
    active = struct.unpack_from("<H", body, 9)[0]
    pos = 11
    fields = []
    for _ in range(9):
        value, pos = tdx_protocol.decode_price(body, pos)
        fields.append(value)
    base, pre_diff, open_diff, high_diff, low_diff, server_time, neg_price, volume, current_volume = fields
    if pos + 4 > len(body):
        raise tdx_protocol.TdxProtocolError("short volume profile amount")
    amount = struct.unpack_from("<f", body, pos)[0]
    pos += 4
    extras = []
    for _ in range(4):
        value, pos = tdx_protocol.decode_price(body, pos)
        extras.append(value)
    bid_levels: list[dict[str, Any]] = []
    ask_levels: list[dict[str, Any]] = []
    for _ in range(3):
        bid, pos = tdx_protocol.decode_price(body, pos)
        ask, pos = tdx_protocol.decode_price(body, pos)
        bid_vol, pos = tdx_protocol.decode_price(body, pos)
        ask_vol, pos = tdx_protocol.decode_price(body, pos)
        bid_levels.append({"price": (base + bid) / 100.0, "volume_lots": bid_vol})
        ask_levels.append({"price": (base + ask) / 100.0, "volume_lots": ask_vol})
    if pos + 2 > len(body):
        raise tdx_protocol.TdxProtocolError("short volume profile footer")
    unknown = struct.unpack_from("<H", body, pos)[0]
    pos += 2
    profiles: list[dict[str, Any]] = []
    profile_price = 0
    for _ in range(count):
        delta, pos = tdx_protocol.decode_price(body, pos)
        delta = _profile_delta(delta)
        vol, pos = tdx_protocol.decode_price(body, pos)
        buy, pos = tdx_protocol.decode_price(body, pos)
        sell, pos = tdx_protocol.decode_price(body, pos)
        profile_price += delta
        profiles.append({"price": profile_price / 100.0, "volume_lots": vol, "buy_lots": buy, "sell_lots": sell})
    return {
        "count": count, "market": market, "code": code, "active": active,
        "close": base / 100.0, "open": (base + open_diff) / 100.0,
        "high": (base + high_diff) / 100.0, "low": (base + low_diff) / 100.0,
        "pre_close": (base + pre_diff) / 100.0, "server_time_raw": server_time,
        "negative_price": neg_price / 100.0, "volume_lots": volume,
        "current_volume_lots": current_volume, "amount": amount,
        "inner_volume_lots": extras[0], "outer_volume_lots": extras[1],
        "s_amount": extras[2], "open_amount": extras[3],
        "bid_levels": bid_levels, "ask_levels": ask_levels, "unknown": unknown,
        "profiles": profiles,
    }


def build_minute_series_request(market: int, code: str, trade_date: date | str | int) -> bytes:
    return _request(MINUTE_SERIES, struct.pack("<IB6s", _date_number(trade_date), market, tdx_protocol._code(code)))


def parse_minute_series(body: bytes) -> list[dict[str, Any]]:
    if len(body) < 6:
        raise tdx_protocol.TdxProtocolError("short minute series response")
    count = struct.unpack_from("<H", body, 0)[0]
    pre_close = struct.unpack_from("<f", body, 2)[0]
    pos, price = 6, 0
    rows = []
    for index in range(count):
        delta, pos = tdx_protocol.decode_price(body, pos)
        unknown, pos = tdx_protocol.decode_price(body, pos)
        volume, pos = tdx_protocol.decode_price(body, pos)
        price += delta
        minute = 571 + index if index < 120 else 661 + index  # 09:31..11:30, then 13:01..15:00
        hour, minute_part = divmod(minute, 60)
        rows.append({"minute_index": index, "time": f"{hour:02d}:{minute_part:02d}:00",
                     "price": price / 100.0, "unknown": unknown, "volume_lots": volume,
                     "pre_close": pre_close})
    return rows


def build_auction_request(market: int, code: str, start: int = 0, count: int = 500) -> bytes:
    payload = struct.pack("<H6sIIII", market, tdx_protocol._code(code), 0, 3, 0, int(start)) + struct.pack("<I", int(count))
    return _request(AUCTION, payload)


def parse_auction(body: bytes) -> list[dict[str, Any]]:
    if len(body) < 2:
        raise tdx_protocol.TdxProtocolError("short auction response")
    count = struct.unpack_from("<H", body, 0)[0]
    rows = []
    for index in range(count):
        pos = 2 + index * 16
        if pos + 16 > len(body):
            raise tdx_protocol.TdxProtocolError("truncated auction response")
        minute, = struct.unpack_from("<H", body, pos)
        price = struct.unpack_from("<f", body, pos + 2)[0]
        matched = struct.unpack_from("<I", body, pos + 6)[0]
        unmatched_raw = struct.unpack_from("<i", body, pos + 10)[0]
        second = body[pos + 15]
        rows.append({"time": f"{minute // 60:02d}:{minute % 60:02d}:{second:02d}",
                     "price": price, "matched_raw": matched,
                     "unmatched_raw": abs(unmatched_raw),
                     "unmatched_side": "B" if unmatched_raw >= 0 else "S"})
    return rows


def build_unusual_request(market: int, start: int = 0, count: int = 600) -> bytes:
    return _request(UNUSUAL, struct.pack("<HII", market, int(start), int(count)))


def _unusual_label(event_type: int, payload: bytes) -> tuple[str, str, bytes | None]:
    first = payload[0]
    v2, v3, v4 = struct.unpack_from("<fff", payload, 1)
    if event_type == 0x03:
        return ("主力买入" if first == 0 else "主力卖出"), f"{v2:.2f}/{v3:.2f}", None
    if event_type == 0x04:
        return "加速拉升", f"{v2 * 100:.2f}%", None
    if event_type == 0x05:
        return "加速下跌", "", None
    if event_type == 0x06:
        return "低位反弹", f"{v2 * 100:.2f}%", None
    if event_type == 0x07:
        return "高位回落", f"{v2 * 100:.2f}%", None
    if event_type == 0x08:
        return "撑杆跳高", f"{v2 * 100:.2f}%", None
    if event_type == 0x09:
        return "平台跳水", f"{v2 * 100:.2f}%", None
    if event_type == 0x0A:
        return ("单笔冲跌" if v2 < 0 else "单笔冲涨"), f"{v2 * 100:.2f}%", None
    if event_type == 0x0B:
        suffix = "平" if v3 == 0 else ("跌" if v3 < 0 else "涨")
        return "区间放量" + suffix, f"{v2:.1f}倍{v3 * 100:.2f}%", None
    if event_type == 0x0C:
        return "区间缩量", "", None
    if event_type == 0x10:
        return "大单托盘", f"{v4:.2f}/{v3:.2f}", None
    if event_type == 0x11:
        return "大单压盘", f"{v2:.2f}/{v3:.2f}", None
    if event_type == 0x12:
        return "大单锁盘", "", None
    if event_type == 0x13:
        return "竞价试买", f"{v2:.2f}/{v3:.2f}", None
    if event_type == 0x14:
        # gotdx: byte 0 is the direction, byte 1 the sub-type, then two floats.
        side = "涨" if first == 0 else "跌"
        label = {1: "逼近{}停", 2: "封{}停板", 4: "封{}大减", 5: "打开{}停"}.get(payload[1])
        u2, u3 = struct.unpack_from("<ff", payload, 2)
        if label is None:
            return f"unknown_0x14_{payload[1]:02x}", f"{u2:.2f}/{u3:.2f}", payload
        return label.format(side), f"{u2:.2f}/{u3:.2f}", None
    if event_type == 0x15:
        # gotdx leaves byte 0 = 0 unnamed ("尾盘??") and calls every value above 2 打压.
        if first == 0:
            return "unknown_0x15_00", f"{v2 * 100:.2f}%/{v3:.2f}", payload
        return {1: "尾盘对倒", 2: "尾盘拉升"}.get(first, "尾盘打压"), f"{v2 * 100:.2f}%/{v3:.2f}", None
    if event_type == 0x16:
        return ("盘中弱势" if v2 < 0 else "盘中强势"), f"{v2 * 100:.2f}%", None
    if event_type == 0x1D:
        return "急速拉升", f"{v2 * 100:.2f}%", None
    if event_type == 0x1E:
        return "急速下跌", f"{v2 * 100:.2f}%", None
    return f"unknown_0x{event_type:02x}", "", payload


def parse_unusual(body: bytes) -> list[dict[str, Any]]:
    if len(body) < 2:
        raise tdx_protocol.TdxProtocolError("short unusual response")
    count = struct.unpack_from("<H", body, 0)[0]
    rows = []
    for index in range(count):
        pos = 2 + index * 32
        if pos + 32 > len(body):
            raise tdx_protocol.TdxProtocolError("truncated unusual response")
        market = struct.unpack_from("<H", body, pos)[0]
        code = tdx_protocol.decode_gbk(body[pos + 2:pos + 8])
        event_type = body[pos + 9]
        sequence = struct.unpack_from("<H", body, pos + 11)[0]
        desc, value, payload_raw_bytes = _unusual_label(event_type, body[pos + 15:pos + 28])
        hour = body[pos + 29]
        minute_second = struct.unpack_from("<H", body, pos + 30)[0]
        row = {"index": sequence, "market": market, "code": code,
               "time": f"{hour:02d}:{minute_second // 100:02d}:{minute_second % 100:02d}",
               "description": desc, "value": value, "event_type": event_type}
        if payload_raw_bytes:
            row["payload_raw"] = payload_raw_bytes.hex()
        rows.append(row)
    return rows


def build_top_board_request(category: int, size: int = 20) -> bytes:
    # Mode 5 and the reserved marker are required by the main-market server.
    payload = struct.pack("<BB7sB", category, 5, b"\x00\x00\x00\x00\x01\x00\x00", size)
    return _request(TOP_BOARD, payload)


def parse_top_board(body: bytes) -> dict[str, list[dict[str, Any]]]:
    if len(body) < 1:
        raise tdx_protocol.TdxProtocolError("short top board response")
    size = body[0]
    names = ("increase", "decrease", "amplitude", "rise_speed", "fall_speed",
             "volume_ratio", "positive_commission_ratio", "negative_commission_ratio", "turnover")
    result: dict[str, list[dict[str, Any]]] = {name: [] for name in names}
    pos = 1
    for name in names:
        for _ in range(size):
            if pos + 15 > len(body):
                raise tdx_protocol.TdxProtocolError("truncated top board response")
            market = body[pos]
            code = tdx_protocol.decode_gbk(body[pos + 1:pos + 7])
            price = struct.unpack_from("<f", body, pos + 7)[0]
            value = struct.unpack_from("<f", body, pos + 11)[0]
            result[name].append({"market": market, "code": code, "price": price, "value": value})
            pos += 15
    return result


def build_minute_data_request(market: int, code: str, start: int = 0, count: int = 240) -> bytes:
    return _request(MINUTE_TIME_DATA, struct.pack("<H6sHH", market, tdx_protocol._code(code), start, count))


def _parse_minute_rows(body: bytes, code: str, history: bool) -> list[dict[str, Any]]:
    minimum = 10 if history else 4
    if len(body) < minimum:
        raise tdx_protocol.TdxProtocolError("short minute response")
    count = struct.unpack_from("<H", body, 0)[0]
    pos = 10 if history else 4
    unit = _unit(code)
    first_price = first_avg = 0
    rows = []
    for index in range(count):
        price_delta, pos = tdx_protocol.decode_price(body, pos)
        avg_delta, pos = tdx_protocol.decode_price(body, pos)
        volume, pos = tdx_protocol.decode_price(body, pos)
        price = price_delta + (first_price if first_price else 0)
        avg = avg_delta + (first_avg if first_avg else 0)
        if not first_price:
            first_price = price
        if not first_avg:
            first_avg = avg
        rows.append({"index": index, "price": price / unit, "average": avg / (unit * 100),
                     "volume_lots": volume})
    return rows


def parse_minute_data(body: bytes, code: str) -> list[dict[str, Any]]:
    return _parse_minute_rows(body, code, False)


def build_history_minute_data_request(market: int, code: str, trade_date: date | str | int) -> bytes:
    payload = struct.pack("<iB6s", -abs(_date_number(trade_date)), market, tdx_protocol._code(code))
    return _request(HISTORY_MINUTE_TIME_DATA, payload)


def parse_history_minute_data(body: bytes, code: str) -> list[dict[str, Any]]:
    return _parse_minute_rows(body, code, True)


async def fetch_volume_profile(*, market: int, code: str) -> CapabilityEvidence:
    result, host = await tdx_protocol.call(
        lambda client: parse_volume_profile(client._exchange(build_volume_profile_request(market, code))),
        handshake_profile="login_one")
    return tdx_protocol.observed_evidence([{"symbol": tdx_protocol.symbol(market, code), **row} for row in result["profiles"]], host)


async def fetch_minute_series(*, market: int, code: str, trade_date: date | str | int) -> CapabilityEvidence:
    rows, host = await tdx_protocol.call(
        lambda client: parse_minute_series(client._exchange(build_minute_series_request(market, code, trade_date))),
        handshake_profile="login_one")
    return tdx_protocol.observed_evidence([{"symbol": tdx_protocol.symbol(market, code), **row} for row in rows], host)


async def fetch_auction_curve(*, market: int, code: str) -> CapabilityEvidence:
    rows, host = await tdx_protocol.call(
        lambda client: parse_auction(client._exchange(build_auction_request(market, code))),
        handshake_profile="login_one")
    return tdx_protocol.observed_evidence([{"symbol": tdx_protocol.symbol(market, code), **row} for row in rows], host)


async def fetch_unusual(*, market: int, start: int = 0, count: int = 600) -> CapabilityEvidence:
    rows, host = await tdx_protocol.call(
        lambda client: parse_unusual(client._exchange(build_unusual_request(market, start, count))),
        handshake_profile="login_one")
    return tdx_protocol.observed_evidence([{"symbol": tdx_protocol.symbol(row["market"], row["code"]), **row} for row in rows], host)


async def fetch_top_board(*, category: int = 0, size: int = 20) -> CapabilityEvidence:
    boards, host = await tdx_protocol.call(
        lambda client: parse_top_board(client._exchange(build_top_board_request(category, size))),
        handshake_profile="login_one")
    return tdx_protocol.observed_evidence([{"symbol": tdx_protocol.symbol(row["market"], row["code"]), "category": name, **row}
                      for name, rows in boards.items() for row in rows], host)


__all__ = [
    "AUCTION", "HISTORY_MINUTE_TIME_DATA", "MINUTE_SERIES", "MINUTE_TIME_DATA", "TOP_BOARD", "UNUSUAL", "VOLUME_PROFILE",
    "build_auction_request", "build_history_minute_data_request", "build_minute_data_request",
    "build_minute_series_request", "build_top_board_request", "build_unusual_request", "build_volume_profile_request",
    "fetch_auction_curve", "fetch_minute_series", "fetch_top_board", "fetch_unusual", "fetch_volume_profile",
    "parse_auction", "parse_history_minute_data", "parse_minute_data", "parse_minute_series",
    "parse_top_board", "parse_unusual", "parse_volume_profile",
]
