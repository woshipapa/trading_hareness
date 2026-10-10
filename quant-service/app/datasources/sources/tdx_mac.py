"""Small stdlib client for the MAC (0x12xx) TDX protocol.

The MAC service is separate from the legacy 0x05xx quote service.  Payloads
are little-endian and strings are fixed-width GBK.
Results are research evidence only: every catalog binding of this source is UNSUPPORTED.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
import struct
import zlib
from datetime import date, datetime, timezone
from typing import Any, Callable, Iterable, Sequence, TypeVar
from zoneinfo import ZoneInfo

from ..contracts import CapabilityEvidence
from . import tdx_protocol

CN_TZ = ZoneInfo("Asia/Shanghai")
_LOGGER = logging.getLogger(__name__)
MAC_HOSTS = (
    ("121.36.248.138", 7709),
    ("123.60.47.136", 7709),
    ("121.37.207.165", 7709),
)
OP_BOARD = 0x1231
OP_MEMBERS = 0x122C
OP_BATCH_QUOTES = 0x122B
OP_BARS = 0x122E
OP_AUCTION = 0x123D
OP_TICK_CHARTS = 0x123E
OP_MARKET_MONITOR = 0x1237
OP_BELONG_BOARD = 0x1218
BAR_PERIODS = {
    "5m": 0,
    "15m": 1,
    "30m": 2,
    "60m": 3,
    "1d": 4,
    "1w": 5,
    "1M": 6,
    "1m_ex": 7,
    "1m": 8,
}
DEFAULT_BITMAP = bytes.fromhex(
    "ff fc f9 cc 3f 08 03 01 00 00 00 00 00 00 00 00 00 00 00 00"
)
#: The bits fetch_limit_prices asks for: 0x13 (server_update_date) and the limit prices 0x20 and 0x21.
LIMITS_BITMAP = bytes.fromhex("00 00 08 00 03 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00")
#: The most symbols one 0x122b request carries.
MAX_BATCH = 80


class TdxMacError(RuntimeError):
    pass


def exchange_board_code(symbol: str) -> int:
    value = symbol.upper()
    if value.startswith("HK"):
        return 20000 + int(value[2:])
    n = int(value)
    if value.startswith("000"):
        return 31000 + n
    if value.startswith("399"):
        return n - 399000 + 30000
    if value.startswith("899"):
        return n - 899000 + 32000
    if value.startswith("88"):
        return n - 880000 + 20000
    return n


def _fixed(value: str, size: int) -> bytes:
    raw = value.encode("utf-8")
    if len(raw) > size:
        raise ValueError(f"value too long for {size}-byte field")
    return raw + bytes(size - len(raw))


def build_request(opcode: int, payload: bytes = b"", *, head: int = 1) -> bytes:
    body = struct.pack("<H", opcode) + payload
    return struct.pack("<BIBHH", head, 0, 1, len(body), len(body)) + body


def build_handshake() -> tuple[bytes, bytes]:
    # The two setup packets used by the public TDX service before MAC calls.
    return (
        bytes.fromhex("0c 02 18 93 00 01 03 00 03 00 0d 00 01".replace(" ", "")),
        bytes.fromhex("0c 02 18 94 00 01 03 00 03 00 0d 00 02".replace(" ", "")),
    )


def build_board_list_request(
    board_type: int = 0, start: int = 0, page_size: int = 150
) -> bytes:
    return build_request(
        OP_BOARD, struct.pack("<HHBBHH8s", page_size, board_type, 0, 1, start, 1, b"")
    )


def build_board_members_request(
    board_code: int,
    start: int = 0,
    page_size: int = 80,
    *,
    quotes: bool = False,
    bitmap: bytes = DEFAULT_BITMAP,
    sort_type: int = 14,
    sort_order: int = 1,
    filter_byte: int = 0,
) -> bytes:
    """Board members (0x122c): names only, or with ``quotes`` the dynamic quote fields of ``bitmap``.

    ``sort_type``, ``sort_order`` and ``filter_byte`` (copied into bitmap byte 17) belong to the quote form."""
    if quotes:
        if len(bitmap) != 20:
            raise ValueError("MAC field bitmap must be 20 bytes")
        fields = bytearray(bitmap)
        fields[17] = filter_byte
        fields[19] |= 1
        payload = struct.pack(
            "<I9sHIHBB20s", board_code, b"", sort_type, start, page_size, sort_order, 0, bytes(fields)
        )
    else:
        payload = struct.pack(
            "<I9sHIBBH20s", board_code, b"", 14, start, page_size, 0, 1, b""
        )
    return build_request(OP_MEMBERS, payload)


def build_batch_quotes_request(
    stocks: Sequence[tuple[int, str]], bitmap: bytes = DEFAULT_BITMAP
) -> bytes:
    if len(bitmap) != 20:
        raise ValueError("MAC field bitmap must be 20 bytes")
    payload = bytearray(bitmap) + struct.pack("<H", len(stocks))
    for market, code in stocks:
        payload += struct.pack("<H22s", market, _fixed(code, 22))
    return build_request(OP_BATCH_QUOTES, bytes(payload))


def build_bars_request(
    market: int,
    code: str,
    period: int = 4,
    start: int = 0,
    count: int = 800,
    adjust: int = 0,
) -> bytes:
    payload = struct.pack(
        "<H22sHHIHHbbbbH4s",
        market,
        _fixed(code, 22),
        period,
        1,
        start,
        count + 1,
        adjust,
        1,
        1,
        0,
        1,
        0,
        b"",
    )
    return build_request(OP_BARS, payload)


def build_aux_request(
    opcode: int,
    market: int,
    code: str,
    *,
    head: int = 1,
    start: int = 0,
    count: int = 500,
) -> bytes:
    if opcode == OP_AUCTION:
        payload = struct.pack("<H22sII10s", market, _fixed(code, 22), start, count, b"")
    elif opcode == OP_TICK_CHARTS:
        payload = struct.pack("<H22sIHH6s", market, _fixed(code, 22), 0, 5, 1, b"")
    elif opcode == OP_BELONG_BOARD:
        payload = struct.pack(
            "<H8s16s21s", market, _fixed(code, 8), b"", _fixed("Stock_GLHQ", 21)
        )
    elif opcode == OP_MARKET_MONITOR:
        payload = struct.pack(
            "<HHHHHH5H", market, start, 0, count, 0, 1, 200, 30, 40, 50, 200
        )
    else:
        raise ValueError(f"unsupported MAC opcode {opcode:#x}")
    return build_request(opcode, payload, head=head)


def _float(data: bytes, offset: int) -> float:
    return struct.unpack_from("<f", data, offset)[0]


def parse_board_list(body: bytes) -> list[dict[str, Any]]:
    if len(body) < 4:
        raise TdxMacError(f"truncated board list response: need 4 bytes, got {len(body)}")
    count_all, _total = struct.unpack_from("<HH", body, 0)
    count = count_all // 2 if count_all // 2 != 0 else count_all
    rows = []
    for i in range(count):
        pos = 4 + i * 160
        if pos + 160 > len(body):
            raise TdxMacError(f"truncated board list item {i}: need {pos + 160} bytes, got {len(body)}")
        rows.append(
            {
                "market": struct.unpack_from("<H", body, pos)[0],
                "code": tdx_protocol.decode_gbk(body[pos + 2 : pos + 8]),
                "name": tdx_protocol.decode_gbk(body[pos + 24 : pos + 68]),
                "price": _float(body, pos + 68),
                "rise_speed": _float(body, pos + 72),
                "pre_close": _float(body, pos + 76),
                "leading_market": struct.unpack_from("<H", body, pos + 80)[0],
                "leading_code": tdx_protocol.decode_gbk(body[pos + 82 : pos + 88]),
                "leading_name": tdx_protocol.decode_gbk(body[pos + 104 : pos + 148]),
                "leading_price": _float(body, pos + 148),
                "leading_rise_speed": _float(body, pos + 152),
                "leading_pre_close": _float(body, pos + 156),
            }
        )
    return rows


def parse_board_members(body: bytes, *, quotes: bool = False) -> list[dict[str, Any]]:
    if len(body) < 26:
        raise TdxMacError(f"truncated board members response: need 26 bytes, got {len(body)}")
    if quotes:
        from .tdx_mac_fields import decode_dynamic_response

        return decode_dynamic_response(body)
    count = struct.unpack_from("<H", body, 24)[0]
    stride = 68
    rows = []
    for i in range(count):
        pos = 26 + i * stride
        if pos + stride > len(body):
            raise TdxMacError(f"truncated board members item {i}: need {pos + stride} bytes, got {len(body)}")
        row = {
            "market": struct.unpack_from("<H", body, pos)[0],
            "symbol": tdx_protocol.decode_gbk(body[pos + 2 : pos + 8]),
            "name": tdx_protocol.decode_gbk(body[pos + 24 : pos + 40]),
        }
        rows.append(row)
    return rows


def parse_batch_quotes(body: bytes, requested: Sequence[tuple[int, str]]) -> list[dict[str, Any]]:
    """Decode a 0x122b answer and check it by position against ``requested`` (delta-1 R1).

    The server answers one row per requested symbol, in order, and a placeholder row for one it does not know
    (an old BJ code comes back as another stock's code at price 0).  A row count that differs raises; a row whose
    market and code differ from its position is dropped and logged as ``code_mismatch``."""
    from .tdx_mac_fields import decode_dynamic_response

    rows = decode_dynamic_response(body)
    if len(rows) != len(requested):
        raise TdxMacError(f"quote answer has {len(rows)} rows for {len(requested)} requested symbols")
    kept = []
    for wanted, row in zip(requested, rows):
        if (row["market"], row["symbol"]) == tuple(wanted):
            kept.append(row)
        else:
            _LOGGER.warning("code_mismatch requested=%s returned=%s", tuple(wanted), (row["market"], row["symbol"]))
    return kept


def parse_bars(body: bytes) -> list[dict[str, Any]]:
    if len(body) < 33:
        raise TdxMacError(f"truncated bars response: need 33 bytes, got {len(body)}")
    count = struct.unpack_from("<H", body, 27)[0]
    rows = []
    for i in range(count):
        pos = 33 + i * 36
        if pos + 36 > len(body):
            raise TdxMacError(f"truncated bar {i}: need {pos + 36} bytes, got {len(body)}")
        ymd, seconds = struct.unpack_from("<II", body, pos)
        rows.append(
            {
                "date": _ymd(ymd).isoformat(),
                "seconds": seconds,
                "open": _float(body, pos + 8),
                "high": _float(body, pos + 12),
                "low": _float(body, pos + 16),
                "close": _float(body, pos + 20),
                "amount": _float(body, pos + 24),
                "volume": _float(body, pos + 28),
            }
        )
    return rows[1:]


def parse_auxiliary_count(opcode: int, body: bytes) -> int:
    if opcode == OP_AUCTION and len(body) >= 28:
        return struct.unpack_from("<I", body, 24)[0]
    if opcode == OP_TICK_CHARTS and len(body) >= 71:
        return struct.unpack_from("<H", body, 69)[0]
    if opcode == OP_MARKET_MONITOR and len(body) >= 2:
        return struct.unpack_from("<H", body, 0)[0]
    if opcode == OP_BELONG_BOARD and len(body) >= 27:
        try:
            value = json.loads(body[27:].decode("gbk", "replace"))
            return len(value) if isinstance(value, list) else 0
        except (ValueError, TypeError, UnicodeError):
            return 0
    return 0


class TdxMacClient:
    def __init__(self, host: str, port: int = 7709, timeout_seconds: float = 5.0):
        self.host, self.port, self.timeout = host, port, timeout_seconds
        self._socket: socket.socket | None = None

    def __enter__(self):
        self._socket = socket.create_connection(
            (self.host, self.port), timeout=self.timeout
        )
        self.handshake()
        return self

    def handshake(self) -> None:
        """Perform the legacy two-packet setup when a MAC host requires it."""
        for packet in build_handshake():
            self._exchange(packet)

    def __exit__(self, *_):
        if self._socket:
            self._socket.close()
            self._socket = None

    def _recv(self, size: int) -> bytes:
        assert self._socket
        out = bytearray()
        while len(out) < size:
            chunk = self._socket.recv(size - len(out))
            if not chunk:
                raise TdxMacError("MAC server closed connection")
            out.extend(chunk)
        return bytes(out)

    def _exchange(self, request: bytes) -> bytes:
        if not self._socket:
            raise TdxMacError("MAC client is not connected")
        self._socket.sendall(request)
        header = self._recv(16)
        zipped, plain = struct.unpack_from("<HH", header, 12)
        body = self._recv(zipped)
        if zipped == plain:
            return body
        try:
            return zlib.decompress(body)
        except zlib.error as error:
            raise TdxMacError("MAC response failed to decompress") from error

    def board_list(self, board_type: int = 0):
        rows = []
        for start in range(0, 10000, 150):
            page = parse_board_list(
                self._exchange(build_board_list_request(board_type, start=start))
            )
            rows.extend(page)
            if len(page) < 150:
                break
        return rows

    def board_members(self, board_symbol: str):
        return self._board_members(board_symbol, quotes=False)

    def _board_members(self, board_symbol: str, *, quotes: bool):
        rows = []
        board_code = exchange_board_code(board_symbol)
        for start in range(0, 10000, 80):
            body = self._exchange(
                build_board_members_request(board_code, start=start, quotes=quotes)
            )
            page = parse_board_members(body, quotes=quotes)
            rows.extend(page)
            total = (
                struct.unpack_from("<I", body, 20)[0] if len(body) >= 24 else len(rows)
            )
            if len(rows) >= total or len(page) < 80:
                break
        return rows

    def board_member_quotes(self, board_symbol: str):
        return self._board_members(board_symbol, quotes=True)

    def batch_quotes(self, stocks: Sequence[tuple[int, str]], bitmap: bytes = DEFAULT_BITMAP):
        rows = []
        for offset in range(0, len(stocks), MAX_BATCH):
            chunk = stocks[offset:offset + MAX_BATCH]
            rows.extend(parse_batch_quotes(self._exchange(build_batch_quotes_request(chunk, bitmap)), chunk))
        return rows

    def bars(
        self, market: int, code: str, period: int = 4, start: int = 0, count: int = 800
    ):
        return parse_bars(
            self._exchange(build_bars_request(market, code, period, start, count))
        )

    def auxiliary(
        self, opcode: int, market: int = 0, code: str = "000001", **kwargs: Any
    ) -> bytes:
        return self._exchange(build_aux_request(opcode, market, code, **kwargs))


T = TypeVar("T")


def configured_hosts(
    environ: dict[str, str] | None = None,
) -> tuple[tuple[str, int], ...]:
    raw = (environ or os.environ).get("TDX_MAC_HOSTS", "").strip()
    if not raw:
        return MAC_HOSTS
    return tuple(
        (host, int(port or 7709))
        for item in raw.split(",")
        for host, _, port in [item.strip().partition(":")]
        if host
    )


def call_sync(
    operation: Callable[[TdxMacClient], T],
    *,
    hosts: Iterable[tuple[str, int]] | None = None,
    timeout_seconds: float = 5.0,
) -> tuple[T, str]:
    errors = []
    for host, port in hosts or configured_hosts():
        try:
            with TdxMacClient(host, port, timeout_seconds) as client:
                return operation(client), f"{host}:{port}"
        except (OSError, TdxMacError) as exc:
            errors.append(f"{host}:{type(exc).__name__}: {exc}")
    raise TdxMacError("no MAC host answered: " + ", ".join(errors[-4:]))


async def call(operation: Callable[[TdxMacClient], T], **kwargs: Any):
    return await asyncio.to_thread(call_sync, operation, **kwargs)


def _ymd(value: int) -> date:
    """A MAC YYYYMMDD number as a date."""
    try:
        return date(value // 10000, value // 100 % 100, value % 100)
    except ValueError as error:
        raise TdxMacError(f"invalid MAC date {value}") from error


def _shanghai_time(day: date, hour: int, minute: int, second: int) -> datetime:
    """A date and a time of day as an Asia/Shanghai-aware time."""
    try:
        return datetime(day.year, day.month, day.day, hour, minute, second, tzinfo=CN_TZ)
    except ValueError as error:
        raise TdxMacError(f"invalid MAC time {hour:02d}:{minute:02d}:{second:02d} on {day}") from error


def _quote_row(row: dict[str, Any]) -> dict[str, Any]:
    """A decoded 0x122b row with its full symbol and ``exchange_time`` built from bits 0x13 and 0x14."""
    update_time = row["server_update_time"]
    consumed = ("symbol", "server_update_date", "server_update_time")
    return {
        "symbol": tdx_protocol.symbol(row["market"], row["symbol"]),
        "exchange_time": _shanghai_time(
            _ymd(row["server_update_date"]), update_time // 10000, update_time // 100 % 100, update_time % 100),
        **{key: value for key, value in row.items() if key not in consumed},
    }


def _limit_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "symbol": tdx_protocol.symbol(row["market"], row["symbol"]),
        "trade_date": _ymd(row["server_update_date"]),
        "limit_up": row["buy_price_limit"],
        "limit_down": row["sell_price_limit"],
    }


def _minute_bar_row(symbol: str, row: dict[str, Any]) -> dict[str, Any]:
    """A decoded 0x122e row with its symbol and ``bar_time`` from the wire date and seconds since midnight."""
    seconds = row["seconds"]
    return {
        "symbol": symbol,
        "bar_time": _shanghai_time(date.fromisoformat(row["date"]), seconds // 3600, seconds // 60 % 60, seconds % 60),
        **{key: value for key, value in row.items() if key not in ("date", "seconds")},
    }


async def fetch_watch_snapshot(*, symbols: Sequence[str]) -> CapabilityEvidence:
    stocks = [tdx_protocol.market_code(symbol) for symbol in symbols]
    rows, host = await call(lambda client: [_quote_row(row) for row in client.batch_quotes(stocks)])
    return tdx_protocol.observed_evidence(rows, host)


async def fetch_limit_prices(*, symbols: Sequence[str]) -> CapabilityEvidence:
    stocks = [tdx_protocol.market_code(symbol) for symbol in symbols]
    rows, host = await call(lambda client: [_limit_row(row) for row in client.batch_quotes(stocks, LIMITS_BITMAP)])
    return tdx_protocol.observed_evidence(rows, host)


async def fetch_board_catalog() -> CapabilityEvidence:
    def collect(client):
        rows = []
        for board_type in range(7):
            if board_type == 2 or board_type == 6:
                continue
            rows.extend({"board_code": row["code"], "name": row["name"],
                         "board_type": board_type}
                        for row in client.board_list(board_type))
        return rows
    rows, host = await call(collect)
    return tdx_protocol.observed_evidence(rows, host)


async def fetch_membership(*, sector_key: str) -> CapabilityEvidence:
    def collect(client):
        # Find board_type from catalog (skip type 6 to avoid duplication)
        board_type = None
        for bt in range(7):
            if bt == 6:
                continue
            for row in client.board_list(bt):
                if row["code"] == sector_key:
                    board_type = bt
                    break
            if board_type is not None:
                break
        if board_type is None:
            raise TdxMacError(f"unknown board key: {sector_key}")

        members = client.board_members(sector_key)
        known_at = datetime.now(timezone.utc)
        rows = []
        for row in members:
            if row["market"] not in tdx_protocol.EXCHANGES:
                raise TdxMacError(f"unknown market id: {row['market']}")
            rows.append({
                "taxonomy_key": f"tdx_mac_type_{board_type}",
                "sector_key": sector_key,
                "symbol": tdx_protocol.symbol(row["market"], row["symbol"]),
                "known_at": known_at,
            })
        return rows
    rows, host = await call(collect)
    return tdx_protocol.observed_evidence(rows, host)


async def fetch_daily_bars(*, symbol: str, count: int) -> CapabilityEvidence:
    market, code = tdx_protocol.market_code(symbol)
    rows, host = await call(
        lambda client: client.bars(market, code, BAR_PERIODS["1d"], 0, count)
    )
    full_symbol = tdx_protocol.symbol(market, code)
    return tdx_protocol.observed_evidence([{"symbol": full_symbol, **row} for row in rows], host)


async def fetch_minute_bars(*, symbol: str, count: int) -> CapabilityEvidence:
    market, code = tdx_protocol.market_code(symbol)
    full_symbol = tdx_protocol.symbol(market, code)
    rows, host = await call(
        lambda client: [_minute_bar_row(full_symbol, row) for row in client.bars(market, code, BAR_PERIODS["1m"], 0, count)]
    )
    return tdx_protocol.observed_evidence(rows, host)


__all__ = [
    "BAR_PERIODS",
    "DEFAULT_BITMAP",
    "LIMITS_BITMAP",
    "MAC_HOSTS",
    "MAX_BATCH",
    "TdxMacClient",
    "TdxMacError",
    "build_aux_request",
    "build_batch_quotes_request",
    "build_bars_request",
    "build_board_list_request",
    "build_board_members_request",
    "build_handshake",
    "call",
    "call_sync",
    "configured_hosts",
    "exchange_board_code",
    "parse_auxiliary_count",
    "parse_bars",
    "parse_batch_quotes",
    "parse_board_list",
    "parse_board_members",
    "fetch_watch_snapshot",
    "fetch_limit_prices",
    "fetch_board_catalog",
    "fetch_membership",
    "fetch_daily_bars",
    "fetch_minute_bars",
]
