"""Legacy TDX microstructure commands.

The command layouts are the public ``gotdx``/``zytdx`` layouts.  This module
keeps the wire builders and parsers pure and uses :class:`TdxClient` only for
the existing setup, framing, timeout and decompression behavior.
"""

from __future__ import annotations

import asyncio
import struct
from datetime import date
from typing import Any, Callable, Iterable, TypeVar

from . import tdx_protocol


VOLUME_PROFILE = 0x051A
MINUTE_TIME_DATA = 0x0537
HISTORY_MINUTE_TIME_DATA = 0x0FEB
AUCTION = 0x056A
TOP_BOARD = 0x053F
UNUSUAL = 0x0563
HISTORY_ORDERS = 0x0FB4

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


def _text(raw: bytes) -> str:
    return raw.split(b"\x00", 1)[0].decode("gbk", "replace").strip()


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
        raise ValueError("short volume profile response")
    count, market = struct.unpack_from("<HB", body, 0)
    code = _text(body[3:9])
    active = struct.unpack_from("<H", body, 9)[0]
    pos = 11
    fields = []
    for _ in range(9):
        value, pos = tdx_protocol.decode_price(body, pos)
        fields.append(value)
    base, pre_diff, open_diff, high_diff, low_diff, server_time, neg_price, volume, current_volume = fields
    if pos + 4 > len(body):
        raise ValueError("short volume profile amount")
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
        raise ValueError("short volume profile footer")
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


def build_history_orders_request(market: int, code: str, trade_date: date | str | int) -> bytes:
    return _request(HISTORY_ORDERS, struct.pack("<IB6s", _date_number(trade_date), market, tdx_protocol._code(code)))


def parse_history_orders(body: bytes) -> list[dict[str, Any]]:
    if len(body) < 6:
        raise ValueError("short history orders response")
    count = struct.unpack_from("<H", body, 0)[0]
    pre_close = struct.unpack_from("<f", body, 2)[0]
    pos, price = 6, 0
    rows = []
    for _ in range(count):
        delta, pos = tdx_protocol.decode_price(body, pos)
        unknown, pos = tdx_protocol.decode_price(body, pos)
        volume, pos = tdx_protocol.decode_price(body, pos)
        price += delta
        rows.append({"price": price / 100.0, "unknown": unknown, "volume_lots": volume,
                     "vol": volume, "pre_close": pre_close})
    return rows


def build_auction_request(market: int, code: str, start: int = 0, count: int = 500) -> bytes:
    payload = struct.pack("<H6sIIII", market, tdx_protocol._code(code), 0, 3, 0, int(start)) + struct.pack("<I", int(count))
    return _request(AUCTION, payload)


def parse_auction(body: bytes) -> list[dict[str, Any]]:
    if len(body) < 2:
        raise ValueError("short auction response")
    count = struct.unpack_from("<H", body, 0)[0]
    rows = []
    for index in range(count):
        pos = 2 + index * 16
        if pos + 16 > len(body):
            raise ValueError("truncated auction response")
        minute, = struct.unpack_from("<H", body, pos)
        price = struct.unpack_from("<f", body, pos + 2)[0]
        matched = struct.unpack_from("<I", body, pos + 6)[0]
        unmatched_raw = struct.unpack_from("<i", body, pos + 10)[0]
        second = body[pos + 15]
        rows.append({"time": f"{minute // 60:02d}:{minute % 60:02d}:{second:02d}",
                     "price": price, "matched_raw": matched,
                     "unmatched_raw": abs(unmatched_raw),
                     "unmatched_side": "B" if unmatched_raw >= 0 else "S",
                     "flag": 1 if unmatched_raw >= 0 else -1})
    return rows


def build_unusual_request(market: int, start: int = 0, count: int = 600) -> bytes:
    return _request(UNUSUAL, struct.pack("<HII", market, int(start), int(count)))


def _unusual_label(event_type: int, payload: bytes) -> tuple[str, str]:
    if len(payload) < 13:
        return "", ""
    first = payload[0]
    v2, v3, v4 = struct.unpack_from("<fff", payload, 1)
    if event_type == 0x03:
        return ("主力买入" if first == 0 else "主力卖出"), f"{v2:.2f}/{v3:.2f}"
    if event_type == 0x04:
        return "加速拉升", f"{v2 * 100:.2f}%"
    if event_type == 0x05:
        return "加速下跌", ""
    if event_type == 0x06:
        return "低位反弹", f"{v2 * 100:.2f}%"
    if event_type == 0x07:
        return "高位回落", f"{v2 * 100:.2f}%"
    if event_type == 0x08:
        return "撑杆跳高", f"{v2 * 100:.2f}%"
    if event_type == 0x09:
        return "平台跳水", f"{v2 * 100:.2f}%"
    if event_type == 0x0A:
        return ("单笔冲跌" if v2 < 0 else "单笔冲涨"), f"{v2 * 100:.2f}%"
    if event_type == 0x0B:
        suffix = "平" if v3 == 0 else ("跌" if v3 < 0 else "涨")
        return "区间放量" + suffix, f"{v2:.1f}倍{v3 * 100:.2f}%"
    if event_type == 0x0C:
        return "区间缩量", ""
    if event_type == 0x10:
        return "大单托盘", f"{v4:.2f}/{v3:.2f}"
    if event_type == 0x11:
        return "大单压盘", f"{v2:.2f}/{v3:.2f}"
    if event_type == 0x12:
        return "大单锁盘", ""
    if event_type == 0x13:
        return "竞价试买", f"{v2:.2f}/{v3:.2f}"
    if event_type == 0x16:
        return ("盘中弱势" if v2 < 0 else "盘中强势"), f"{v2 * 100:.2f}%"
    if event_type == 0x1D:
        return "急速拉升", f"{v2 * 100:.2f}%"
    if event_type == 0x1E:
        return "急速下跌", f"{v2 * 100:.2f}%"
    return "", ""


def parse_unusual(body: bytes) -> list[dict[str, Any]]:
    if len(body) < 2:
        raise ValueError("short unusual response")
    count = struct.unpack_from("<H", body, 0)[0]
    rows = []
    for index in range(count):
        pos = 2 + index * 32
        if pos + 32 > len(body):
            raise ValueError("truncated unusual response")
        market = struct.unpack_from("<H", body, pos)[0]
        code = _text(body[pos + 2:pos + 8])
        event_type = body[pos + 9]
        sequence = struct.unpack_from("<H", body, pos + 11)[0]
        desc, value = _unusual_label(event_type, body[pos + 15:pos + 28])
        hour = body[pos + 29]
        minute_second = struct.unpack_from("<H", body, pos + 30)[0]
        rows.append({"index": sequence, "market": market, "code": code,
                     "time": f"{hour:02d}:{minute_second // 100:02d}:{minute_second % 100:02d}",
                     "description": desc, "value": value, "event_type": event_type})
    return rows


def build_top_board_request(category: int, size: int = 20) -> bytes:
    # Mode 5 and the reserved marker are required by the main-market server.
    payload = struct.pack("<BB7sB", category, 5, b"\x00\x00\x00\x00\x01\x00\x00", size)
    return _request(TOP_BOARD, payload)


def parse_top_board(body: bytes) -> dict[str, Any]:
    if len(body) < 1:
        raise ValueError("short top board response")
    size = body[0]
    names = ("increase", "decrease", "amplitude", "rise_speed", "fall_speed",
             "volume_ratio", "positive_commission_ratio", "negative_commission_ratio", "turnover")
    result: dict[str, Any] = {name: [] for name in names}
    pos = 1
    for name in names:
        for _ in range(size):
            if pos + 15 > len(body):
                raise ValueError("truncated top board response")
            market = body[pos]
            code = _text(body[pos + 1:pos + 7])
            price = struct.unpack_from("<f", body, pos + 7)[0]
            value = struct.unpack_from("<f", body, pos + 11)[0]
            result[name].append({"market": market, "code": code, "price": price, "value": value})
            pos += 15
    result["size"] = size  # type: ignore[assignment]
    return result


def build_minute_data_request(market: int, code: str, start: int = 0, count: int = 240) -> bytes:
    return _request(MINUTE_TIME_DATA, struct.pack("<H6sHH", market, tdx_protocol._code(code), start, count))


def _parse_minute_rows(body: bytes, code: str, history: bool) -> list[dict[str, Any]]:
    minimum = 10 if history else 4
    if len(body) < minimum:
        raise ValueError("short minute response")
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
        rows.append({"index": index, "price": price / unit, "avg": avg / (unit * 100),
                     "average": avg / (unit * 100), "volume_lots": volume, "vol": volume})
    return rows


def parse_minute_data(body: bytes, code: str) -> list[dict[str, Any]]:
    return _parse_minute_rows(body, code, False)


def build_history_minute_data_request(market: int, code: str, trade_date: date | str | int) -> bytes:
    payload = struct.pack("<iB6s", -abs(_date_number(trade_date)), market, tdx_protocol._code(code))
    return _request(HISTORY_MINUTE_TIME_DATA, payload)


def parse_history_minute_data(body: bytes, code: str) -> list[dict[str, Any]]:
    return _parse_minute_rows(body, code, True)


class TdxMicrostructureClient(tdx_protocol.TdxClient):
    """TDX client with the microstructure command family."""

    def volume_profile(self, market: int, code: str) -> dict[str, Any]:
        return parse_volume_profile(self._exchange(build_volume_profile_request(market, code)))

    def history_orders(self, market: int, code: str, trade_date: date | str | int) -> list[dict[str, Any]]:
        return parse_history_orders(self._exchange(build_history_orders_request(market, code, trade_date)))

    def auction(self, market: int, code: str) -> list[dict[str, Any]]:
        return parse_auction(self._exchange(build_auction_request(market, code)))

    def unusual(self, market: int, start: int = 0, count: int = 600) -> list[dict[str, Any]]:
        return parse_unusual(self._exchange(build_unusual_request(market, start, count)))

    def top_board(self, category: int = 0, size: int = 20) -> dict[str, Any]:
        return parse_top_board(self._exchange(build_top_board_request(category, size)))

    def minute_data(self, market: int, code: str) -> list[dict[str, Any]]:
        return parse_minute_data(self._exchange(build_minute_data_request(market, code)), code)

    def history_minute_data(self, market: int, code: str, trade_date: date | str | int) -> list[dict[str, Any]]:
        return parse_history_minute_data(self._exchange(build_history_minute_data_request(market, code, trade_date)), code)


T = TypeVar("T")


def call_sync(operation: Callable[[TdxMicrostructureClient], T], *,
              hosts: Iterable[tuple[str, int]] | None = None, timeout_seconds: float = 5.0) -> tuple[T, str]:
    errors = []
    for host, port in hosts or tdx_protocol.configured_hosts():
        try:
            with TdxMicrostructureClient(host, port, timeout_seconds) as client:
                return operation(client), f"{host}:{port}"
        except (OSError, tdx_protocol.TdxProtocolError, struct.error, IndexError, ValueError) as error:
            errors.append(f"{host}:{type(error).__name__}")
    raise tdx_protocol.TdxProtocolError("no TDX host answered: " + ", ".join(errors[-4:]))


async def _fetch(operation: Callable[[TdxMicrostructureClient], T]) -> T:
    value, _host = await asyncio.to_thread(call_sync, operation)
    return value


async def fetch_volume_profile(*, market: int, code: str) -> dict[str, Any]:
    return await _fetch(lambda client: client.volume_profile(market, code))


async def fetch_history_orders(*, market: int, code: str, trade_date: date | str | int) -> list[dict[str, Any]]:
    return await _fetch(lambda client: client.history_orders(market, code, trade_date))


async def fetch_auction_curve(*, market: int, code: str) -> list[dict[str, Any]]:
    return await _fetch(lambda client: client.auction(market, code))


async def fetch_unusual(*, market: int, start: int = 0, count: int = 600) -> list[dict[str, Any]]:
    return await _fetch(lambda client: client.unusual(market, start, count))


async def fetch_top_board(*, category: int = 0, size: int = 20) -> dict[str, Any]:
    return await _fetch(lambda client: client.top_board(category, size))


__all__ = [
    "AUCTION", "HISTORY_MINUTE_TIME_DATA", "HISTORY_ORDERS", "MINUTE_TIME_DATA", "TOP_BOARD",
    "UNUSUAL", "VOLUME_PROFILE", "TdxMicrostructureClient", "build_auction_request",
    "build_history_minute_data_request", "build_history_orders_request", "build_minute_data_request",
    "build_top_board_request", "build_unusual_request", "build_volume_profile_request", "call_sync",
    "fetch_auction_curve", "fetch_history_orders", "fetch_top_board", "fetch_unusual", "fetch_volume_profile",
    "parse_auction", "parse_history_minute_data", "parse_history_orders", "parse_minute_data",
    "parse_top_board", "parse_unusual", "parse_volume_profile",
]
