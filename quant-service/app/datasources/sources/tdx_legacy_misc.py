"""Read-only decoders for the older TDX HQ commands.

The module deliberately keeps this surface separate from ``tdx_protocol``.  It
uses the same variable integer and framed socket primitives, but the client
subclass sends only the first setup packet because the legacy commands are
accepted by older hosts before the newer capability negotiation.
"""

from __future__ import annotations

import math
import struct
from datetime import date
from typing import Any, Sequence

from ..contracts import CapabilityEvidence
from . import tdx_protocol


KMSG_EXCHANGEANNOUNCE = 0x0002
KMSG_HEARTBEAT = 0x0004
KMSG_ANNOUNCEMENT = 0x000A
KMSG_TODOB = 0x000B
KMSG_PING = 0x0015
KMSG_SECURITYFEATURE452 = 0x0452
KMSG_INDEXMOMENTUM = 0x051C
KMSG_INDEXINFO = 0x051D
KMSG_SECURITYBARS_OFFSET = 0x052D
KMSG_QUOTESENCRYPT = 0x0547
KMSG_QUOTESLIST = 0x054B
KMSG_QUOTES_BATCH = 0x054C
KMSG_CHARTSAMPLING = 0x0FD1
KMSG_TRANSACTIONDATA = 0x0FC5
KMSG_HISTORYTRANSACTIONDATA = 0x0FB5
KMSG_TRANSACTIONDATA_TRANS = 0x0FC6
KMSG_TODOFDE = 0x0FDE

MAX_LEGACY_QUOTES = 500
MAX_BATCH_QUOTES = 500
RANKING_PAGE_SIZE = 80

# Category values observed in pytdx/gotdx and the official desktop client.
QUOTE_CATEGORIES = {
    "sh_a": 0,
    "sz_a": 2,
    "all_a": 6,
    "b_shares": 7,
    "star": 8,
    "bj": 12,
    "chinext": 14,
}
QUOTE_FILTERS = {"new": 1, "star": 2, "st": 4, "chinext": 8, "bj": 16}
QUOTE_SORT_TYPES = {
    "code": 0, "name": 1, "pre_close": 2, "open": 3, "high": 4, "low": 5,
    "price": 6, "bid": 7, "ask": 8, "volume": 9, "amount": 10,
    "last_volume": 11, "change": 12, "change_pct": 14,
}
TRANSACTION_ACTIONS = {0: "BUY", 1: "SELL", 2: "NEUTRAL", 5: "P", 8: "A"}


def _all_a_snapshot(client: tdx_protocol.TdxClient) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    while True:
        request = build_quotes_list_request(QUOTE_CATEGORIES["all_a"], QUOTE_SORT_TYPES["code"], len(rows),
                                            RANKING_PAGE_SIZE)
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


def _code(code: str) -> bytes:
    raw = code.encode("ascii")
    if len(raw) != 6 or not raw.isdigit():
        raise ValueError("TDX codes are six ASCII digits")
    return raw


def _header(opcode: int, payload: bytes = b"", *, packet_type: int = 0) -> bytes:
    length = len(payload) + 2
    return struct.pack("<BIBHHH", 0x0C, 0, packet_type, length, length, opcode) + payload


def _float32(data: bytes, pos: int) -> tuple[float, int]:
    if pos + 4 > len(data):
        raise ValueError("truncated float32")
    return struct.unpack_from("<f", data, pos)[0], pos + 4


def _text(raw: bytes) -> str:
    raw = raw.split(b"\x00", 1)[0]
    for encoding in ("gbk", "utf-8"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin1", "replace")


def build_quotes_list_request(category: int, sort_type: int, start: int = 0, count: int = 80,
                              sort_reverse: bool = False, mode: int = 5, filter: int = 0) -> bytes:
    count = min(max(0, count), MAX_LEGACY_QUOTES)
    payload = struct.pack("<9H", category, sort_type, start, count, int(sort_reverse), mode, filter, 1, 0)
    return _header(KMSG_QUOTESLIST, payload)


def build_quotes_batch_request(stocks: Sequence[tuple[int, str]]) -> bytes:
    stocks = stocks[:MAX_BATCH_QUOTES]
    payload = struct.pack("<H6sH", 5, b"\x00" * 6, len(stocks))
    payload += b"".join(struct.pack("<B6s", market, _code(code)) for market, code in stocks)
    return _header(KMSG_QUOTES_BATCH, payload, packet_type=1)


def _parse_quote_item(data: bytes, pos: int, *, encrypted: bool = False) -> tuple[dict[str, Any], int]:
    start = pos
    if pos + 9 > len(data):
        raise ValueError("truncated quote item")
    market = data[pos]
    code = _text(data[pos + 1:pos + 7])
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
    amount, pos = _float32(data, pos)
    inner, pos = tdx_protocol.decode_price(data, pos)
    outer, pos = tdx_protocol.decode_price(data, pos)
    s_amount, pos = tdx_protocol.decode_price(data, pos)
    open_amount, pos = tdx_protocol.decode_price(data, pos)
    levels = []
    level_count = 5 if encrypted else 1
    for _ in range(level_count):
        bid, pos = tdx_protocol.decode_price(data, pos)
        ask, pos = tdx_protocol.decode_price(data, pos)
        bid_vol, pos = tdx_protocol.decode_price(data, pos)
        ask_vol, pos = tdx_protocol.decode_price(data, pos)
        levels.append({"bid": (base + bid) / 100, "ask": (base + ask) / 100,
                       "bid_volume": bid_vol, "ask_volume": ask_vol})
    if encrypted:
        if pos + 10 > len(data):
            raise ValueError("truncated encrypted quote tail")
        pos += 10
        # Six reserved four-field levels follow the visible five levels.
        for _ in range(6):
            for _ in range(4):
                _, pos = tdx_protocol.decode_price(data, pos)
    else:
        if pos + 2 > len(data):
            raise ValueError("truncated quote tail")
        unknown = struct.unpack_from("<H", data, pos)[0]
        pos += 2
        rise_speed = struct.unpack_from("<h", data, pos)[0]
        pos += 2
        short_turnover = struct.unpack_from("<h", data, pos)[0]
        pos += 2
        min2_amount, pos = _float32(data, pos)
        opening_rush = struct.unpack_from("<h", data, pos)[0]
        pos += 2
        pos += 10
        vol_rise_speed, pos = _float32(data, pos)
        depth, pos = _float32(data, pos)
        pos += 24
        active2 = struct.unpack_from("<H", data, pos)[0]
        pos += 2
    item: dict[str, Any] = {
        "market": market, "code": code, "active1": active1, "active2": 0 if encrypted else active2,
        "price": base / 100, "close": base / 100, "pre_close": (base + pre_diff) / 100,
        "open": (base + open_diff) / 100, "high": (base + high_diff) / 100,
        "low": (base + low_diff) / 100, "server_time_raw": server_time,
        "neg_price": neg_price / 100, "volume": volume, "current_volume": current_volume,
        "amount": amount, "inner_volume": inner, "outer_volume": outer,
        "s_amount": s_amount, "open_amount": open_amount, "levels": levels,
        "wire_bytes": pos - start,
    }
    if not encrypted:
        item.update({"unknown": unknown, "rise_speed": rise_speed / 100, "short_turnover": short_turnover / 100,
                     "min2_amount": min2_amount, "opening_rush": opening_rush / 100,
                     "vol_rise_speed": vol_rise_speed, "depth": depth})
    return item, pos


def parse_quotes_list(body: bytes) -> list[dict[str, Any]]:
    if len(body) < 4:
        return []
    count = struct.unpack_from("<H", body, 2)[0]
    pos, rows = 4, []
    for _ in range(count):
        row, pos = _parse_quote_item(body, pos)
        rows.append(row)
    return rows


def parse_quotes_batch(body: bytes) -> list[dict[str, Any]]:
    return parse_quotes_list(body)


def build_encrypted_quotes_request(stocks: Sequence[tuple[int, str]]) -> bytes:
    stocks = stocks[:MAX_BATCH_QUOTES]
    payload = struct.pack("<H", len(stocks))
    payload += b"".join(struct.pack("<B6sHH", market, _code(code), 22234, 2) for market, code in stocks)
    return _header(KMSG_QUOTESENCRYPT, payload, packet_type=1)


def parse_encrypted_quotes(body: bytes) -> list[dict[str, Any]]:
    decoded = bytes(value ^ 0x93 for value in body)
    if len(decoded) < 2:
        return []
    count = struct.unpack_from("<H", decoded, 0)[0]
    pos, rows = 2, []
    for _ in range(count):
        row, pos = _parse_quote_item(decoded, pos, encrypted=True)
        rows.append(row)
    return rows


def build_index_info_request(market: int, code: str) -> bytes:
    return _header(KMSG_INDEXINFO, struct.pack("<H6sI", market, _code(code), 0))


def parse_index_info(body: bytes) -> dict[str, Any]:
    if len(body) < 16:
        return {}
    order_count = struct.unpack_from("<I", body, 0)[0]
    market, code, active = body[4], _text(body[5:11]), struct.unpack_from("<H", body, 11)[0]
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
    amount, pos = _float32(body, pos)
    # The protocol has a sparse run of legacy fields; up/down counts are the
    # two values immediately following five unused pairs (as in gotdx).
    for _ in range(2):
        _, pos = tdx_protocol.decode_price(body, pos)
    open_amount, pos = tdx_protocol.decode_price(body, pos)
    for _ in range(3):
        _, pos = tdx_protocol.decode_price(body, pos)
    up_count, pos = tdx_protocol.decode_price(body, pos)
    down_count, pos = tdx_protocol.decode_price(body, pos)
    for _ in range(10):
        _, pos = tdx_protocol.decode_price(body, pos)
    orders, last_price = [], 0
    for _ in range(min(order_count, 10000)):
        delta, pos = tdx_protocol.decode_price(body, pos)
        unknown, pos = tdx_protocol.decode_price(body, pos)
        vol, pos = tdx_protocol.decode_price(body, pos)
        last_price += delta
        orders.append({"price": last_price / 100, "unknown": unknown, "volume": vol})
    return {"order_count": order_count, "market": market, "code": code, "active": active,
            "close": close / 100, "pre_close": (close + pre_diff) / 100, "diff": -pre_diff / 100,
            "open": (close + open_diff) / 100, "high": (close + high_diff) / 100,
            "low": (close + low_diff) / 100, "server_time_raw": server_time, "after_hour": after_hour,
            "volume": volume, "current_volume": current_volume, "amount": amount,
            "open_amount": open_amount, "up_count": up_count, "down_count": down_count, "orders": orders}


def build_index_momentum_request(market: int, code: str) -> bytes:
    return _header(KMSG_INDEXMOMENTUM, struct.pack("<H6s", market, _code(code)))


def parse_index_momentum(body: bytes) -> list[int]:
    if len(body) < 2:
        return []
    count = struct.unpack_from("<H", body, 0)[0]
    pos, total, values = 2, 0, []
    for _ in range(count):
        delta, pos = tdx_protocol.decode_price(body, pos)
        total += delta
        values.append(total)
    return values


def build_chart_sampling_request(market: int, code: str) -> bytes:
    reserved = bytes.fromhex("00" * 16 + "010014000000000100000000")
    return _header(KMSG_CHARTSAMPLING, struct.pack("<H6s", market, _code(code)) + reserved, packet_type=1)


def parse_chart_sampling(body: bytes) -> dict[str, Any]:
    if len(body) < 42:
        return {}
    market = struct.unpack_from("<H", body, 0)[0]
    code = _text(body[2:8])
    count = struct.unpack_from("<H", body, 34)[0]
    pre_close = struct.unpack_from("<f", body, 36)[0]
    pos, prices = 42, []
    for _ in range(count):
        if pos + 4 > len(body):
            break
        prices.append(struct.unpack_from("<f", body, pos)[0])
        pos += 4
    return {"market": market, "code": code, "count": count, "pre_close": pre_close, "prices": prices}


def build_transaction_request(opcode: int, market: int, code: str, start: int = 0, count: int = 200) -> bytes:
    if opcode not in (KMSG_TRANSACTIONDATA, KMSG_TRANSACTIONDATA_TRANS):
        raise ValueError("opcode must be 0x0fc5 or 0x0fc6")
    return _header(opcode, struct.pack("<H6sHH", market, _code(code), start, count))


def _parse_transaction_rows(body: bytes, *, with_direction: bool, history: bool = False,
                            trade_date: date | None = None) -> list[dict[str, Any]]:
    if len(body) < (6 if history else 2):
        return []
    count = struct.unpack_from("<H", body, 0)[0]
    pos = 6 if history else 2
    pre_close = struct.unpack_from("<f", body, 2)[0] if history and with_direction else None
    rows, last_price = [], 0
    for _ in range(count):
        if pos + 2 > len(body):
            break
        minutes = struct.unpack_from("<H", body, pos)[0]
        pos += 2
        delta, pos = tdx_protocol.decode_price(body, pos)
        volume, pos = tdx_protocol.decode_price(body, pos)
        if history and with_direction:
            num, pos = tdx_protocol.decode_price(body, pos)
            if pos + 2 > len(body):
                break
            direction = struct.unpack_from("<H", body, pos)[0]
            pos += 2
            unknown = 0
        else:
            num, pos = tdx_protocol.decode_price(body, pos) if with_direction else (None, pos)
            direction, pos = tdx_protocol.decode_price(body, pos)
            unknown, pos = tdx_protocol.decode_price(body, pos)
        last_price += delta
        row = {"time": f"{minutes // 60:02d}:{minutes % 60:02d}", "price_raw": last_price,
               "volume": volume, "num": num, "direction": direction,
               "action": TRANSACTION_ACTIONS.get(direction, str(direction)),
               "unknown": unknown}
        if pre_close is not None:
            row["pre_close"] = pre_close
        if trade_date is not None:
            row["date"] = trade_date.isoformat()
        rows.append(row)
    return rows


def parse_transaction_data(body: bytes) -> list[dict[str, Any]]:
    return _parse_transaction_rows(body, with_direction=True)


def build_history_transaction_request(opcode: int, market: int, code: str, trade_date: date,
                                       start: int = 0, count: int = 2000) -> bytes:
    if opcode not in (KMSG_HISTORYTRANSACTIONDATA, KMSG_TRANSACTIONDATA_TRANS):
        raise ValueError("opcode must be 0x0fb5 or 0x0fc6")
    payload = struct.pack("<IH6sHH", int(trade_date.strftime("%Y%m%d")), market, _code(code), start, count)
    return _header(opcode, payload)


def parse_history_transaction_data(body: bytes, *, trade_date: date | None = None,
                                   with_direction: bool = False) -> list[dict[str, Any]]:
    return _parse_transaction_rows(body, with_direction=with_direction, history=True, trade_date=trade_date)


def build_security_feature452_request(start: int = 0, count: int = 2000) -> bytes:
    return _header(KMSG_SECURITYFEATURE452, struct.pack("<IIIH", start, count, 1, 0), packet_type=1)


def parse_security_feature452(body: bytes) -> list[dict[str, Any]]:
    if len(body) < 2:
        return []
    count = struct.unpack_from("<H", body, 0)[0]
    pos, rows = 2, []
    for _ in range(count):
        if pos + 13 > len(body):
            break
        market = body[pos]
        code_num = struct.unpack_from("<I", body, pos + 1)[0]
        p1 = struct.unpack_from("<f", body, pos + 5)[0]
        p2 = struct.unpack_from("<f", body, pos + 9)[0]
        rows.append({"market": market, "code": f"{code_num:06d}", "p1": p1, "p2": p2})
        pos += 13
    return rows


def build_security_bars_offset_request(category: int, market: int, code: str, start: int = 0,
                                       count: int = 800) -> bytes:
    payload = struct.pack("<H6sHHHHIIH", market, _code(code), category, 1, start, count, 0, 0, 0)
    return _header(KMSG_SECURITYBARS_OFFSET, payload)


def build_simple_request(opcode: int, payload: bytes = b"", *, packet_type: int = 1) -> bytes:
    return _header(opcode, payload, packet_type=packet_type)


def build_exchange_announcement_request() -> bytes:
    return build_simple_request(KMSG_EXCHANGEANNOUNCE)


def build_announcement_request() -> bytes:
    return build_simple_request(KMSG_ANNOUNCEMENT, bytes(54))


def build_ping_request() -> bytes:
    return build_simple_request(KMSG_PING, packet_type=0)


def build_heartbeat_request() -> bytes:
    return build_simple_request(KMSG_HEARTBEAT)


def build_todob_request() -> bytes:
    return build_simple_request(KMSG_TODOB)


def build_todofde_request() -> bytes:
    return build_simple_request(KMSG_TODOFDE)


def parse_heartbeat(body: bytes) -> dict[str, Any]:
    return {"date": struct.unpack_from("<I", body, 6)[0]} if len(body) >= 10 else {}


def parse_exchange_announcement(body: bytes) -> dict[str, Any]:
    return {"version": body[0], "content": _text(body[1:])} if body else {}


def parse_announcement(body: bytes) -> dict[str, Any]:
    if not body or body[0] != 1 or len(body) < 11:
        return {"has_content": False, "raw_length": len(body)}
    stamp, title_len, author_len, content_len = struct.unpack_from("<IHHH", body, 1)
    end = 11 + title_len + author_len + content_len
    if end > len(body):
        return {"has_content": False, "raw_length": len(body)}
    pos = 11
    title, author, content = (_text(body[pos:pos + title_len]), _text(body[pos + title_len:pos + title_len + author_len]),
                              _text(body[pos + title_len + author_len:end]))
    return {"has_content": True, "expire_date": f"{stamp // 10000:04d}-{stamp % 10000 // 100:02d}-{stamp % 100:02d}",
            "title": title, "author": author, "content": content, "raw_length": len(body)}


def parse_raw(body: bytes) -> dict[str, Any]:
    return {"raw_length": len(body), "hex": body.hex(), "text": _text(body)}


def parse_ping(body: bytes) -> dict[str, Any]:
    return parse_raw(body)


def parse_todob(body: bytes) -> dict[str, Any]:
    return parse_raw(body)


def parse_todofde(body: bytes) -> dict[str, Any]:
    return parse_raw(body)


class LegacyMiscClient(tdx_protocol.TdxClient):
    """TDX client that performs the legacy-compatible one-packet setup."""

    def __enter__(self) -> "LegacyMiscClient":
        self._socket = __import__("socket").create_connection((self.host, self.port), timeout=self.timeout)
        self._exchange(tdx_protocol._SETUP_COMMANDS[0])
        return self

    def _call(self, request: bytes) -> bytes:
        return self._exchange(request)

    def quotes_list(self, **kwargs: Any) -> list[dict[str, Any]]:
        return parse_quotes_list(self._call(build_quotes_list_request(**kwargs)))

    def quotes_batch(self, stocks: Sequence[tuple[int, str]]) -> list[dict[str, Any]]:
        return parse_quotes_batch(self._call(build_quotes_batch_request(stocks)))

    def quotes_encrypted(self, stocks: Sequence[tuple[int, str]]) -> list[dict[str, Any]]:
        return parse_encrypted_quotes(self._call(build_encrypted_quotes_request(stocks)))

    def index_info(self, market: int, code: str) -> dict[str, Any]:
        return parse_index_info(self._call(build_index_info_request(market, code)))

    def index_momentum(self, market: int, code: str) -> list[int]:
        return parse_index_momentum(self._call(build_index_momentum_request(market, code)))

    def chart_sampling(self, market: int, code: str) -> dict[str, Any]:
        return parse_chart_sampling(self._call(build_chart_sampling_request(market, code)))

    def transaction_data(self, market: int, code: str, start: int = 0, count: int = 200) -> list[dict[str, Any]]:
        """Current-session transactions (0x0fc5, direction is varint encoded)."""
        return parse_transaction_data(self._call(build_transaction_request(KMSG_TRANSACTIONDATA, market, code, start, count)))

    def transaction_data_trans(self, market: int, code: str, trade_date: date,
                               start: int = 0, count: int = 2000) -> list[dict[str, Any]]:
        """Historical transactions with explicit uint16 direction (0x0fc6)."""
        body = self._call(build_history_transaction_request(KMSG_TRANSACTIONDATA_TRANS, market, code, trade_date, start, count))
        return parse_history_transaction_data(body, trade_date=trade_date, with_direction=True)

    def history_transaction_data(self, market: int, code: str, trade_date: date, start: int = 0,
                                 count: int = 2000, with_direction: bool = True) -> list[dict[str, Any]]:
        opcode = KMSG_TRANSACTIONDATA_TRANS if with_direction else KMSG_HISTORYTRANSACTIONDATA
        body = self._call(build_history_transaction_request(opcode, market, code, trade_date, start, count))
        return parse_history_transaction_data(body, trade_date=trade_date, with_direction=with_direction)

    def security_feature452(self, start: int = 0, count: int = 2000) -> list[dict[str, Any]]:
        return parse_security_feature452(self._call(build_security_feature452_request(start, count)))

    def bars_offset(self, category: int, market: int, code: str, start: int = 0, count: int = 800) -> list[dict[str, Any]]:
        return tdx_protocol.parse_bars(category, self._call(build_security_bars_offset_request(category, market, code, start, count)))

    def raw_command(self, opcode: int, payload: bytes = b"", *, packet_type: int = 1) -> bytes:
        return self._call(build_simple_request(opcode, payload, packet_type=packet_type))
