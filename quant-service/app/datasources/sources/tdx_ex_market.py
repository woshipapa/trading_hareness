"""Read-only TdxExHq/MACEx client for the extended market service (7727).

The extended service is a separate protocol from :mod:`tdx_protocol`: requests
start with ``01`` (rather than ``0c``), carry a 10-byte header and a uint16
method, and responses use the normal TDX ``b1 cb 74 00`` response envelope.
No credentials are involved; the login payload is the public client hello.
"""

from __future__ import annotations

import asyncio
import socket
import struct
import zlib
from typing import Any, Iterable

from .tdx_protocol import TdxProtocolError

PROVIDER_KEY = "tdx_ext"
UPSTREAM_SITE = "tdx-exhq:7727"
DEFAULT_HOSTS = (
    ("112.74.214.43", 7727),
    ("120.25.218.6", 7727),
    ("47.107.75.159", 7727),
    ("47.106.204.218", 7727),
    ("116.205.143.214", 7727),
    ("124.71.223.19", 7727),
)

CATEGORY_TYPES = {
    1: "stock",
    2: "hk",
    3: "futures",
    4: "forex",
    5: "index",
    6: "valuation",
    7: "money",
    8: "fund",
    9: "monetary_fund",
    10: "indicator",
    11: "mirror",
    12: "option",
    13: "us",
    14: "germany",
    15: "singapore",
}
MARKET_IDS = {
    2: "HK alternate",
    4: "Zhengzhou futures options",
    5: "Dalian futures options",
    6: "Shanghai futures options",
    7: "CFFEX options",
    8: "Shanghai stock options",
    9: "SZ stock options",
    10: "basic forex",
    11: "cross forex",
    12: "international indices",
    13: "US",
    16: "COMEX futures",
    17: "NYMEX futures",
    18: "CBOT futures",
    23: "HK financial futures",
    24: "HK financial options",
    25: "HK stock futures",
    26: "HK stock options",
    27: "HK indices",
    28: "Zhengzhou futures",
    29: "Dalian futures",
    30: "Shanghai futures",
    31: "HK main board",
    42: "futures indices",
    33: "open-end fund",
    34: "monetary fund",
    38: "macro indicator",
    43: "B-to-H",
    44: "NEEQ",
    46: "Shanghai gold",
    47: "CFFEX futures",
    48: "HK GEM",
    49: "HK funds",
    54: "treasury valuation",
    60: "main futures",
    62: "CSI indices",
    65: "Guangzhou arbitrage futures",
    66: "Guangzhou futures",
    67: "Guangzhou futures options",
    68: "risk-control indices",
    69: "Huazheng indices",
    70: "extended sector indices",
    71: "HK stocks",
    73: "Germany stocks",
    74: "US stocks",
    75: "international indices",
    78: "Singapore stocks",
    91: "money market",
    93: "fund valuation",
    98: "HK dark pool",
    100: "code mirror",
    102: "SZ indices",
}

COMMANDS = {
    "login": 0x2454,
    "count": 0x23F0,
    "categories": 0x23F4,
    "instruments": 0x23F5,
    "kline": 0x23FF,
    "quote_single": 0x23FA,
}

EX_LOGIN_PAYLOAD = bytes.fromhex(
    "e5bb1c2fafe525941f32c6e5d53dfb415b734cc9cdbf0ac92021bfdd1eb06d22"
    "d008884c1611cb1378f6abd824d899d21f32c6e5d53dfb411f32c6e5d53dfb41"
    "a9325ac935dc0837335a16e4ce17c1bb"
)
EX_SETUP_PAYLOAD = bytes.fromhex(
    "1f32c6e5d53dfb41" * 8 + "cce16dffd5ba3fb8cbc57a054f7748ea"
)


class TdxExMarketError(TdxProtocolError):
    pass


def _text(raw: bytes) -> str:
    return raw.split(b"\0", 1)[0].decode("gbk", "replace").strip()


def _frame(method: int, payload: bytes = b"", *, control: int = 1) -> bytes:
    body = struct.pack("<H", method) + payload
    return struct.pack("<BIBHH", 0x01, 0, control, len(body), len(body)) + body


def build_setup() -> bytes:
    """Build the public TdxExHq setup/hello frame."""
    # The historical ExSetupCmd1 hello uses a non-zero client marker.  This is
    # distinct from normal ExHq requests, whose marker is zero.
    return (
        bytes.fromhex("01 01 48 65 00 01 52 00 52 00")
        + struct.pack("<H", COMMANDS["login"])
        + EX_SETUP_PAYLOAD
    )


def build_login() -> bytes:
    return _frame(COMMANDS["login"], EX_LOGIN_PAYLOAD)


def _code(code: str, size: int = 9) -> bytes:
    raw = code.encode("ascii")
    if len(raw) > size:
        raise ValueError(f"extended code exceeds {size} bytes: {code!r}")
    return raw.ljust(size, b"\0")


def build_count() -> bytes:
    return _frame(COMMANDS["count"])


def build_categories() -> bytes:
    return _frame(COMMANDS["categories"])


def build_instruments(start: int = 0, count: int = 100) -> bytes:
    return _frame(COMMANDS["instruments"], struct.pack("<IH", start, count))


def build_quote(market: int, code: str) -> bytes:
    return _frame(COMMANDS["quote_single"], struct.pack("<B9s", market, _code(code)))




def build_kline(
    category: int, market: int, code: str, start: int = 0, count: int = 800
) -> bytes:
    return _frame(
        COMMANDS["kline"],
        struct.pack(
            "<B9sHHIH", market, _code(code), category, 1, start, min(count, 800)
        ),
    )




def _category_for_market(market_id: int) -> int:
    """Get the kline category (9 for daily equities, 4 for futures daily)."""
    futures_markets = {28, 29, 30, 47, 60, 66, 67}
    return 4 if market_id in futures_markets else 9




def _quote(data: bytes, *, code_len: int = 9) -> dict[str, Any]:
    if len(data) < 1 + code_len:
        raise TdxExMarketError("short quote response")
    market = data[0]
    code = _text(data[1 : 1 + code_len])
    pos = 1 + code_len
    if code_len == 9:
        pos += 4  # single-quote response has a four-byte reserved field
    if len(data) < pos + 140:
        raise TdxExMarketError("short quote response")
    pre, op, hi, lo, price = struct.unpack_from("<5f", data, pos)
    pos += 20
    # Reserved field at +20
    pos += 4
    # Unknown field at +24
    pos += 4
    total, current = struct.unpack_from("<II", data, pos)
    pos += 8
    amount = struct.unpack_from("<f", data, pos)[0]
    pos += 4
    inner, outer = struct.unpack_from("<II", data, pos)
    pos += 8
    hold = struct.unpack_from("<I", data, pos)[0]
    pos += 4
    open_interest = struct.unpack_from("<I", data, pos)[0]
    pos += 4
    bid = list(struct.unpack_from("<5f", data, pos))
    pos += 20
    bid_vol = list(struct.unpack_from("<5I", data, pos))
    pos += 20
    ask = list(struct.unpack_from("<5f", data, pos))
    pos += 20
    ask_vol = list(struct.unpack_from("<5I", data, pos))
    return {
        "market_id": market,
        "code": code,
        "pre_close": pre,
        "open": op,
        "high": hi,
        "low": lo,
        "price": price,
        "open_interest": open_interest,
        "volume": total,
        "amount": amount,
        "server_time": None,
        "current_volume": current,
        "inner_volume": inner,
        "outer_volume": outer,
        "hold_position": hold,
        "bid": bid,
        "bid_volume": bid_vol,
        "ask": ask,
        "ask_volume": ask_vol,
    }


def parse_count(data: bytes) -> int:
    return struct.unpack_from("<I", data, 19)[0] if len(data) >= 23 else 0


def parse_categories(data: bytes) -> list[dict[str, Any]]:
    if len(data) < 2:
        return []
    n = struct.unpack_from("<H", data)[0]
    out = []
    for i in range(n):
        p = 2 + i * 64
        if p + 64 > len(data):
            break
        category_type, name, market_id, abbr = (
            data[p],
            _text(data[p + 1 : p + 33]),
            data[p + 33],
            _text(data[p + 34 : p + 36]),
        )
        out.append(
            {
                "market_id": market_id,
                "market_id_name": MARKET_IDS.get(market_id, "unknown"),
                "category": category_type,
                "category_name": CATEGORY_TYPES.get(category_type, "unknown"),
                "name": name,
                "abbr": abbr,
            }
        )
    return out


def parse_instruments(data: bytes) -> list[dict[str, Any]]:
    if len(data) < 6:
        return []
    n = struct.unpack_from("<H", data, 4)[0]
    out = []
    for i in range(n):
        p = 6 + i * 64
        if p + 64 > len(data):
            break
        out.append(
            {
                "category": data[p],
                "market_id": data[p + 1],
                "code": _text(data[p + 5 : p + 14]),
                "name": _text(data[p + 14 : p + 31]),
                "desc": _text(data[p + 31 : p + 40]),
            }
        )
    return out


def _kline_time(raw: bytes, category: int) -> str:
    if category < 4 or category in (7, 8):
        day, minutes = struct.unpack("<HH", raw)
        year = (day >> 11) + 2004
        md = day & 2047
        return f"{year:04d}-{md // 100:02d}-{md % 100:02d} {minutes // 60:02d}:{minutes % 60:02d}"
    stamp = struct.unpack("<I", raw)[0]
    return f"{stamp // 10000:04d}-{stamp % 10000 // 100:02d}-{stamp % 100:02d}"


def parse_klines(data: bytes, category: int, market_id: int | None = None, code: str | None = None) -> list[dict[str, Any]]:
    if len(data) < 20:
        return []
    n = struct.unpack_from("<H", data, 18)[0]
    out = []
    p = 20
    for _ in range(n):
        if p + 32 > len(data):
            break
        stamp = _kline_time(data[p : p + 4], category)
        op, hi, lo, cl, amount = struct.unpack_from("<5f", data, p + 4)
        position = struct.unpack_from("<I", data, p + 20)[0]
        volume = struct.unpack_from("<I", data, p + 24)[0]
        aux = struct.unpack_from("<f", data, p + 28)[0]
        row = {
            "datetime": stamp,
            "open": op,
            "high": hi,
            "low": lo,
            "close": cl,
            "volume_raw": volume,
            "position": position,
            "price": aux,
        }
        if category == 4:
            # Futures: 'amount' field holds open-interest bits
            row["open_interest"] = struct.unpack_from("<I", struct.pack("<f", amount))[0]
        else:
            row["amount"] = amount
        if market_id is not None:
            row["market_id"] = market_id
        if code is not None:
            row["code"] = code
        out.append(row)
        p += 32
    return out




class TdxExMarketClient:
    """One blocking, single-connection extended-market client."""

    def __init__(
        self, host: str, port: int = 7727, timeout_seconds: float = 5.0
    ) -> None:
        self.host, self.port, self.timeout = host, port, timeout_seconds
        self._socket: socket.socket | None = None

    def __enter__(self) -> "TdxExMarketClient":
        self._socket = socket.create_connection(
            (self.host, self.port), timeout=self.timeout
        )
        self._exchange(build_setup())
        self.login()
        return self

    def __exit__(self, *_exc: object) -> None:
        if self._socket:
            self._socket.close()
            self._socket = None

    def _recv_exact(self, n: int) -> bytes:
        assert self._socket is not None
        chunks = []
        while n:
            part = self._socket.recv(n)
            if not part:
                raise TdxExMarketError("server closed connection")
            chunks.append(part)
            n -= len(part)
        return b"".join(chunks)

    def _exchange(self, request: bytes) -> bytes:
        if self._socket is None:
            raise TdxExMarketError("not connected")
        self._socket.sendall(request)
        header = self._recv_exact(16)
        if header[:4] != bytes.fromhex("b1cb7400"):
            raise TdxExMarketError(f"unexpected response prefix: {header[:4].hex()}")
        zipped, unzipped = struct.unpack_from("<HH", header, 12)
        body = self._recv_exact(zipped)
        if zipped != unzipped:
            body = zlib.decompress(body)
        return body

    def login(self) -> None:
        self._exchange(build_login())

    def count(self) -> int:
        return parse_count(self._exchange(build_count()))

    def categories(self) -> list[dict[str, Any]]:
        return parse_categories(self._exchange(build_categories()))

    def instruments(self, start: int = 0, count: int = 100) -> list[dict[str, Any]]:
        return parse_instruments(self._exchange(build_instruments(start, count)))

    def instrument_pages(
        self, page_size: int = 100, max_pages: int | None = None
    ) -> list[dict[str, Any]]:
        self.login()
        total = self.count()
        rows = []
        pages = 0
        for start in range(0, total, page_size):
            if max_pages is not None and pages >= max_pages:
                break
            chunk = self.instruments(start, page_size)
            rows.extend(chunk)
            pages += 1
            if len(chunk) < page_size:
                break
        return rows

    def quote(self, market: int, code: str) -> dict[str, Any]:
        return _quote(self._exchange(build_quote(market, code)))

    def klines(
        self, category: int, market: int, code: str, start: int = 0, count: int = 800
    ) -> list[dict[str, Any]]:
        return parse_klines(
            self._exchange(build_kline(category, market, code, start, count)), category, market, code
        )


def call_sync(
    operation,
    *,
    hosts: Iterable[tuple[str, int]] = DEFAULT_HOSTS,
    timeout_seconds: float = 5.0,
):
    errors = []
    for host, port in hosts:
        try:
            with TdxExMarketClient(host, port, timeout_seconds) as client:
                return operation(client), f"{host}:{port}"
        except (
            OSError,
            TdxExMarketError,
            struct.error,
            zlib.error,
        ) as error:
            errors.append(f"{host}:{type(error).__name__}")
    raise TdxExMarketError("no extended-market host answered: " + ", ".join(errors))


async def fetch_instruments(
    *, hosts: Iterable[tuple[str, int]] = DEFAULT_HOSTS
) -> list[dict[str, Any]]:
    rows, _host = await asyncio.to_thread(
        lambda: call_sync(lambda client: client.instrument_pages(), hosts=hosts)
    )
    return rows


async def fetch_quote(
    *, market_id: int, code: str, hosts: Iterable[tuple[str, int]] = DEFAULT_HOSTS
) -> list[dict[str, Any]]:
    row, _host = await asyncio.to_thread(
        lambda: call_sync(lambda client: client.quote(market_id, code), hosts=hosts)
    )
    return [row]


async def fetch_bars_daily(
    *, market_id: int, code: str, hosts: Iterable[tuple[str, int]] = DEFAULT_HOSTS
) -> list[dict[str, Any]]:
    category = _category_for_market(market_id)
    rows, _host = await asyncio.to_thread(
        lambda: call_sync(lambda client: client.klines(category, market_id, code), hosts=hosts)
    )
    return rows


__all__ = [
    "COMMANDS",
    "DEFAULT_HOSTS",
    "EX_LOGIN_PAYLOAD",
    "EX_SETUP_PAYLOAD",
    "CATEGORY_TYPES",
    "MARKET_IDS",
    "TdxExMarketClient",
    "TdxExMarketError",
    "build_setup",
    "build_login",
    "build_count",
    "build_categories",
    "build_instruments",
    "build_quote",
    "build_kline",
    "parse_count",
    "parse_categories",
    "parse_instruments",
    "parse_klines",
    "call_sync",
    "fetch_instruments",
    "fetch_quote",
    "fetch_bars_daily",
]
