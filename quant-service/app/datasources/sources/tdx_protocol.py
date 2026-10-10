"""Minimal, standard-library TDX (通达信) quote-protocol client.

Only the read commands this platform needs are implemented: batch Level-1
quotes, K-line bars (daily and 1/5/15/30/60-minute), today's and historical
tick prints (分笔, with buy/sell side), and the ex-rights / share-capital
change log (除权除息与股本变迁).

Why not the ``pytdx`` package: it ships as an sdist that pulls in
``cryptography``/``click`` and cannot be installed from the peer's offline
wheelhouse.  The wire format below follows pytdx's parsers (MIT licence,
https://github.com/rainx/pytdx) and is checked against pytdx on the same
server by ``scripts/verify-tdx-protocol.py``.

The public quote hosts are community-listed and unofficial: every result is
research evidence, and the client fails over across hosts on any error.

The client uses LOGIN_ONE (the first setup packet) by default, with a
legacy-three-packet fallback on the same host when a handshake or operation
fails. Results retain the selected profile in provenance.
"""

from __future__ import annotations

import asyncio
import os
import socket
import struct
import zlib
from datetime import date, datetime, timezone
import logging

from ..contracts import CapabilityEvidence
from .tdx_bj_codes import OLD_TO_NEW
import time
from collections.abc import Mapping
from typing import Any, Callable, Iterable, Sequence, TypeVar


PROVIDER_KEY = "tdx_public"
UPSTREAM_SITE = "tdx-hq:7709"
#: Hosts that answered the historical-tick and ex-rights commands from the
#: owner peer on 2026-09-18 (the first three were compared row by row with
#: pytdx).  Override with ``TDX_HQ_HOSTS=host:port,host:port``.
try:
    from .tdx_hosts import HOSTS as _GENERATED_HOSTS
except ImportError:  # owner probe concatenates this module without package imports
    _GENERATED_HOSTS: tuple[tuple[str, int], ...] = ()

DEFAULT_HOSTS: tuple[tuple[str, int], ...] = _GENERATED_HOSTS or (
        ("60.191.117.167", 7709), ("218.75.126.9", 7709), ("123.125.108.14", 7709),
        ("115.238.56.198", 7709), ("180.153.18.170", 7709), ("124.71.187.122", 7709),
        ("123.60.73.44", 7709), ("183.60.224.178", 7709), ("115.238.90.165", 7709),
        ("111.229.247.189", 7709), ("110.41.147.114", 7709), ("116.205.183.150", 7709),
    )
MARKETS = {"SZ": 0, "SH": 1, "BJ": 2}
MAX_QUOTES_PER_REQUEST = 80
MAX_TICKS_PER_REQUEST = 2000
MAX_BARS_PER_REQUEST = 800
RESPONSE_HEADER_LEN = 16

#: K-line category codes of the protocol.
BAR_CATEGORIES = {"5m": 0, "15m": 1, "30m": 2, "60m": 3, "1d": 9, "1w": 5, "1M": 6, "1m": 8, "1q": 10, "1y": 11}

XDXR_CATEGORIES = {
    1: "除权除息", 2: "送配股上市", 3: "非流通股上市", 4: "未知股本变动", 5: "股本变化", 6: "增发新股",
    7: "股份回购", 8: "增发新股上市", 9: "转配股上市", 10: "可转债上市", 11: "扩缩股", 12: "非流通股缩股",
    13: "送认购权证", 14: "送认沽权证",
}

_SETUP_COMMANDS = (
    bytes.fromhex("0c 02 18 93 00 01 03 00 03 00 0d 00 01".replace(" ", "")),
    bytes.fromhex("0c 02 18 94 00 01 03 00 03 00 0d 00 02".replace(" ", "")),
    bytes.fromhex((
        "0c 03 18 99 00 01 20 00 20 00 db 0f d5 d0 c9 cc d6 a4 a8 af 00 00 00 8f c2 25"
        " 40 13 00 00 d5 00 c9 cc bd f0 d7 ea 00 00 00 02"
    ).replace(" ", "")),
)
HANDSHAKE_PROFILES = {"login_one", "legacy_3"}
_COOLDOWN_SECONDS = 300.0
_COOLDOWN_UNTIL: dict[tuple[str, int], float] = {}
_LOGGER = logging.getLogger(__name__)


class TdxProtocolError(RuntimeError):
    """The server closed, timed out or sent an undecodable response."""


def decode_gbk(raw: bytes) -> str:
    """A fixed-width protocol text field: GBK up to the first NUL. Never tried as UTF-8 first, because short
    GBK names can be valid UTF-8 (\u901a22\u8f6c\u503a would decode to mojibake); a name cut inside a
    character keeps a visible U+FFFD. Padding spaces are stripped."""
    return raw.split(b"\0", 1)[0].decode("gb18030", "replace").strip()


def decode_text(data: bytes) -> str:
    """Decode TDX text members, accepting UTF-8 and GB18030 snapshots."""
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("gb18030", "replace")


# -- decoding primitives (ported from pytdx.helper) -------------------------

def decode_price(data: bytes | bytearray, pos: int) -> tuple[int, int]:
    """Signed variable-length integer: 6 bits in the first byte, then 7."""
    byte = data[pos]
    value = byte & 0x3F
    negative = bool(byte & 0x40)
    shift = 6
    while byte & 0x80:
        pos += 1
        byte = data[pos]
        value += (byte & 0x7F) << shift
        shift += 7
    return (-value if negative else value), pos + 1


def decode_volume(raw: int) -> float:
    """TDX's packed floating amount (a custom exponent/mantissa format)."""
    logpoint = raw >> 24
    hleax = (raw >> 16) & 0xFF
    lheax = (raw >> 8) & 0xFF
    lleax = raw & 0xFF
    dw_ecx = logpoint * 2 - 0x7F
    dw_edx = logpoint * 2 - 0x86
    dw_esi = logpoint * 2 - 0x8E
    dw_eax = logpoint * 2 - 0x96
    xmm6 = pow(2.0, abs(dw_ecx))
    if dw_ecx < 0:
        xmm6 = 1.0 / xmm6
    if hleax > 0x80:
        xmm4 = pow(2.0, dw_edx) * 128.0 + (hleax & 0x7F) * pow(2.0, dw_edx + 1)
    elif dw_edx >= 0:
        xmm4 = pow(2.0, dw_edx) * hleax
    else:
        xmm4 = (1 / pow(2.0, dw_edx)) * hleax
    xmm3 = pow(2.0, dw_esi) * lheax
    xmm1 = pow(2.0, dw_eax) * lleax
    if hleax & 0x80:
        xmm3 *= 2.0
        xmm1 *= 2.0
    return xmm6 + xmm4 + xmm3 + xmm1


#: Minute-level K-line categories. Their records carry volume (shares) and amount (yuan) as IEEE float32;
#: daily and longer bars keep TDX's packed format (TDX plan delta 3, Q8; scripts/probe-tdx-q-units.py).
MINUTE_BAR_CATEGORIES = frozenset({0, 1, 2, 3, 8})


def _float32(raw: int) -> float:
    """IEEE float32 bits; a zero minute of the closing call arrives as a denormal (5.877e-39), read as 0."""
    (value,) = struct.unpack("<f", struct.pack("<I", raw))
    return 0.0 if abs(value) < 1e-20 else value


def _bar_datetime(category: int, data: bytes, pos: int) -> tuple[str, int]:
    if category < 4 or category in (7, 8):
        zipday, minutes = struct.unpack("<HH", data[pos:pos + 4])
        year = (zipday >> 11) + 2004
        month, day = (zipday % 2048) // 100, (zipday % 2048) % 100
        return f"{year:04d}-{month:02d}-{day:02d} {minutes // 60:02d}:{minutes % 60:02d}", pos + 4
    (packed,) = struct.unpack("<I", data[pos:pos + 4])
    return f"{packed // 10000:04d}-{packed % 10000 // 100:02d}-{packed % 100:02d}", pos + 4


# -- command builders and parsers -------------------------------------------

def _code(code: str) -> bytes:
    raw = code.encode("ascii")
    if len(raw) != 6:
        raise ValueError("TDX codes are six ASCII digits")
    return raw


def build_quotes_request(stocks: Sequence[tuple[int, str]]) -> bytes:
    length = len(stocks) * 7 + 12
    header = struct.pack("<HIHHIIHH", 0x10C, 0x02006320, length, length, 0x5053E, 0, 0, len(stocks))
    return header + b"".join(struct.pack("<B6s", market, _code(code)) for market, code in stocks)


def parse_quotes(body: bytes, *, requested: Sequence[tuple[int, str]] | None = None) -> list[dict[str, Any]]:
    pos = 2
    (count,) = struct.unpack("<H", body[pos:pos + 2])
    pos += 2
    quotes = []
    for _ in range(count):
        market, code, _active1 = struct.unpack("<B6sH", body[pos:pos + 9])
        pos += 9
        values = []
        for _field in range(9):  # price, 4 diffs, servertime, reserved, vol, cur_vol
            value, pos = decode_price(body, pos)
            values.append(value)
        price, last_close_diff, open_diff, high_diff, low_diff, server_time, _r1, volume, current_volume = values
        (amount_raw,) = struct.unpack("<I", body[pos:pos + 4])
        pos += 4
        sell_volume, pos = decode_price(body, pos)
        buy_volume, pos = decode_price(body, pos)
        _, pos = decode_price(body, pos)
        _, pos = decode_price(body, pos)
        levels = []
        for _level in range(5):
            bid, pos = decode_price(body, pos)
            ask, pos = decode_price(body, pos)
            bid_volume, pos = decode_price(body, pos)
            ask_volume, pos = decode_price(body, pos)
            levels.append((bid, ask, bid_volume, ask_volume))
        pos += 2
        for _reserved in range(4):
            _, pos = decode_price(body, pos)
        speed_raw, _active2 = struct.unpack("<hH", body[pos:pos + 4])
        pos += 4
        quote: dict[str, Any] = {
            "market": market, "code": code.decode("ascii"),
            "price": price / 100, "last_close": (price + last_close_diff) / 100,
            "open": (price + open_diff) / 100, "high": (price + high_diff) / 100, "low": (price + low_diff) / 100,
            "server_time_raw": server_time, "volume_lots": volume, "current_volume_lots": current_volume,
            "amount": decode_volume(amount_raw), "outer_volume_lots": buy_volume, "inner_volume_lots": sell_volume,
            "speed_pct": speed_raw / 100.0,
        }
        for index, (bid, ask, bid_volume, ask_volume) in enumerate(levels, start=1):
            quote[f"bid{index}"] = (price + bid) / 100
            quote[f"ask{index}"] = (price + ask) / 100
            quote[f"bid_vol{index}"] = bid_volume
            quote[f"ask_vol{index}"] = ask_volume
        quotes.append(quote)
    if requested is None:
        return quotes
    # R1 (delta-1 1c): the server answers one row per requested symbol, in order, and answers an unknown one
    # (an old BJ code) with a placeholder row such as 600839 at 0.0. Compare by position, so a placeholder
    # never passes because its code was also requested elsewhere.
    if len(quotes) != len(requested):
        raise TdxProtocolError(f"quote answer has {len(quotes)} rows for {len(requested)} requested symbols")
    kept = []
    for wanted, quote in zip(requested, quotes):
        if (quote["market"], quote["code"]) == tuple(wanted):
            kept.append(quote)
        else:
            _LOGGER.warning("code_mismatch requested=%s returned=%s", tuple(wanted), (quote["market"], quote["code"]))
    return kept


def build_bars_request(category: int, market: int, code: str, start: int, count: int) -> bytes:
    return struct.pack("<HIHHHH6sHHHHIIH", 0x10C, 0x01016408, 0x1C, 0x1C, 0x052D, market, _code(code),
                       category, 1, start, count, 0, 0, 0)


def parse_bars(category: int, body: bytes) -> list[dict[str, Any]]:
    (count,) = struct.unpack("<H", body[0:2])
    pos = 2
    bars = []
    base = 0
    decode = _float32 if category in MINUTE_BAR_CATEGORIES else decode_volume
    for _ in range(count):
        stamp, pos = _bar_datetime(category, body, pos)
        open_diff, pos = decode_price(body, pos)
        close_diff, pos = decode_price(body, pos)
        high_diff, pos = decode_price(body, pos)
        low_diff, pos = decode_price(body, pos)
        volume_raw, amount_raw = struct.unpack("<II", body[pos:pos + 8])
        pos += 8
        open_value = open_diff + base
        bars.append({
            "datetime": stamp, "open": open_value / 1000, "close": (open_value + close_diff) / 1000,
            "high": (open_value + high_diff) / 1000, "low": (open_value + low_diff) / 1000,
            "volume": decode(volume_raw), "amount": decode(amount_raw),
        })
        base = open_value + close_diff
    return bars


def build_ticks_request(market: int, code: str, start: int, count: int) -> bytes:
    return bytes.fromhex("0c17080101010e000e00c50f") + struct.pack("<H6sHH", market, _code(code), start, count)


def build_history_ticks_request(market: int, code: str, trade_date: date, start: int, count: int) -> bytes:
    stamp = int(trade_date.strftime("%Y%m%d"))
    return bytes.fromhex("0c013001000112001200b50f") + struct.pack("<IH6sHH", stamp, market, _code(code), start, count)


#: Tick side codes, checked against Tencent's B/S prints minute by minute
#: (100% volume agreement for 0/1 on 000001.SZ, 2026-09-18).  ``8`` are the
#: zero-volume 09:15-09:25 call-auction indicative matches; ``5`` are the
#: after-hours fixed-price prints of the STAR/ChiNext boards.
TICK_SIDES = {0: "B", 1: "S", 2: "N", 5: "P", 8: "A"}


def _side(code: int) -> str:
    return TICK_SIDES.get(code, "N")


def parse_ticks(body: bytes, *, history: bool) -> list[dict[str, Any]]:
    (count,) = struct.unpack("<H", body[:2])
    pos = 6 if history else 2
    ticks = []
    price = 0
    for _ in range(count):
        (minutes,) = struct.unpack("<H", body[pos:pos + 2])
        pos += 2
        delta, pos = decode_price(body, pos)
        volume, pos = decode_price(body, pos)
        prints = None
        if not history:
            prints, pos = decode_price(body, pos)
        side, pos = decode_price(body, pos)
        _, pos = decode_price(body, pos)
        price += delta
        ticks.append({"time": f"{minutes // 60:02d}:{minutes % 60:02d}", "price": price / 100,
                      "volume_lots": volume, "prints": prints, "side": _side(side), "side_code": side})
    return ticks


def build_xdxr_request(market: int, code: str) -> bytes:
    return bytes.fromhex("0c1f187600010b000b000f000100") + struct.pack("<B6s", market, _code(code))


def parse_xdxr(body: bytes) -> list[dict[str, Any]]:
    if len(body) < 11:
        return []
    (count,) = struct.unpack("<H", body[9:11])
    pos = 11
    rows = []
    for _ in range(count):
        pos += 8  # market, code, one unused byte
        stamp, pos = _bar_datetime(9, body, pos)
        category = body[pos]
        pos += 1
        chunk = body[pos:pos + 16]
        pos += 16
        row: dict[str, Any] = {"date": stamp, "category": category,
                               "category_name": XDXR_CATEGORIES.get(category, str(category))}
        if category == 1:
            cash, rights_price, bonus_shares, rights_shares = struct.unpack("<ffff", chunk)
            row.update({"cash_dividend_per_10": cash, "rights_price": rights_price,
                        "bonus_shares_per_10": bonus_shares, "rights_shares_per_10": rights_shares})
        elif category in (11, 12):
            row["split_ratio"] = struct.unpack("<IIfI", chunk)[2]
        elif category in (13, 14):
            strike, _, units, _ = struct.unpack("<fIfI", chunk)
            row.update({"warrant_strike": strike, "warrant_units": units})
        else:
            before_float, before_total, after_float, after_total = struct.unpack("<IIII", chunk)
            # Share counts are in units of 10,000 shares.
            row.update({key: (decode_volume(value) if value else 0.0) for key, value in (
                ("float_shares_before_10k", before_float), ("total_shares_before_10k", before_total),
                ("float_shares_after_10k", after_float), ("total_shares_after_10k", after_total))})
        rows.append(row)
    return rows


# -- transport ---------------------------------------------------------------

def configured_hosts(environ: dict[str, str] | None = None) -> tuple[tuple[str, int], ...]:
    raw = (environ if environ is not None else os.environ).get("TDX_HQ_HOSTS", "").strip()
    if not raw:
        return DEFAULT_HOSTS
    hosts = []
    for item in raw.split(","):
        host, _, port = item.strip().partition(":")
        if host:
            hosts.append((host, int(port or 7709)))
    return tuple(hosts) or DEFAULT_HOSTS


class TdxClient:
    """One blocking connection; use :func:`call` for failover and threading."""

    def __init__(self, host: str, port: int, timeout_seconds: float = 5.0, *, handshake_profile: str = "login_one") -> None:
        if handshake_profile not in HANDSHAKE_PROFILES:
            raise ValueError(f"unknown TDX handshake profile: {handshake_profile}")
        self.host, self.port, self.timeout, self.handshake_profile = host, port, timeout_seconds, handshake_profile
        self._socket: socket.socket | None = None
        self._connected = False

    def __enter__(self) -> "TdxClient":
        self._socket = socket.create_connection((self.host, self.port), timeout=self.timeout)
        self._connected = True
        commands = _SETUP_COMMANDS[:1] if self.handshake_profile == "login_one" else _SETUP_COMMANDS
        for command in commands:
            self._exchange(command)
        return self

    def __exit__(self, *_exc: object) -> None:
        if self._socket is not None:
            self._socket.close()
            self._socket = None

    def _recv_exact(self, size: int) -> bytes:
        assert self._socket is not None
        chunks, remaining = [], size
        while remaining:
            chunk = self._socket.recv(remaining)
            if not chunk:
                raise TdxProtocolError("TDX server closed the connection")
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)

    def _exchange(self, request: bytes) -> bytes:
        if self._socket is None:
            raise TdxProtocolError("TDX client is not connected")
        self._socket.sendall(request)
        header = self._recv_exact(RESPONSE_HEADER_LEN)
        _, _, _, zipped, unzipped = struct.unpack("<IIIHH", header)
        body = self._recv_exact(zipped)
        if zipped != unzipped:
            try:
                body = zlib.decompress(body)
            except zlib.error as error:
                raise TdxProtocolError("TDX response failed to decompress") from error
        return body

    def quotes(self, stocks: Sequence[tuple[int, str]]) -> list[dict[str, Any]]:
        result = []
        for offset in range(0, len(stocks), MAX_QUOTES_PER_REQUEST):
            chunk = stocks[offset:offset + MAX_QUOTES_PER_REQUEST]
            result.extend(parse_quotes(self._exchange(build_quotes_request(chunk)), requested=chunk))
        return result

    def bars(self, category: int, market: int, code: str, start: int = 0, count: int = MAX_BARS_PER_REQUEST) -> list[dict[str, Any]]:
        body = self._exchange(build_bars_request(category, market, code, start, min(count, MAX_BARS_PER_REQUEST)))
        return parse_bars(category, body)

    def ticks(self, market: int, code: str, trade_date: date | None = None, *, max_requests: int = 20) -> list[dict[str, Any]]:
        """Every print of a session, oldest first (today when ``trade_date`` is None)."""
        pages: list[list[dict[str, Any]]] = []
        for page in range(max_requests):
            start = page * MAX_TICKS_PER_REQUEST
            if trade_date is None:
                body = self._exchange(build_ticks_request(market, code, start, MAX_TICKS_PER_REQUEST))
                rows = parse_ticks(body, history=False)
            else:
                body = self._exchange(build_history_ticks_request(market, code, trade_date, start, MAX_TICKS_PER_REQUEST))
                rows = parse_ticks(body, history=True)
            pages.append(rows)
            if len(rows) < MAX_TICKS_PER_REQUEST:
                break
        # Each request walks backwards from the newest print.
        return [tick for rows in reversed(pages) for tick in rows]

    def xdxr(self, market: int, code: str) -> list[dict[str, Any]]:
        return parse_xdxr(self._exchange(build_xdxr_request(market, code)))


T = TypeVar("T")


def _ordered_hosts(hosts: Iterable[tuple[str, int]]) -> list[tuple[str, int]]:
    now = time.monotonic()
    values = list(hosts)
    return [item[1] for item in sorted(enumerate(values),
                                       key=lambda item: (_COOLDOWN_UNTIL.get(item[1], 0.0) > now, item[0]))]


def _mark_cooldown(host: tuple[str, int]) -> None:
    _COOLDOWN_UNTIL[host] = time.monotonic() + _COOLDOWN_SECONDS


def call_sync(operation: Callable[[TdxClient], T], *, hosts: Iterable[tuple[str, int]] | None = None,
              timeout_seconds: float = 5.0, handshake_profile: str = "login_one") -> tuple[T, str]:
    """Run an operation with deterministic host ordering and same-host profile fallback."""
    errors: list[str] = []
    for host, port in _ordered_hosts(hosts or configured_hosts()):
        profiles = (handshake_profile, "legacy_3") if handshake_profile == "login_one" else (handshake_profile,)
        transport_failed = False
        for attempt, attempt_profile in enumerate(profiles):
            client = TdxClient(host, port, timeout_seconds, handshake_profile=attempt_profile)
            try:
                with client:
                    result = operation(client)
                return result, f"{host}:{port}/{attempt_profile}"
            except (OSError, TdxProtocolError, struct.error, IndexError, ValueError) as error:
                errors.append(f"{host}:{attempt_profile}:{type(error).__name__}")
                transport_failed = transport_failed or isinstance(error, (OSError, TdxProtocolError))
                if attempt == 0 and len(profiles) == 2 and client._connected:
                    _LOGGER.info("TDX profile fallback host=%s:%s first=%s fallback=legacy_3 error=%s",
                                 host, port, handshake_profile, type(error).__name__)
                    continue
                if attempt == 0 and len(profiles) == 2 and not client._connected:
                    break
        if transport_failed:
            _mark_cooldown((host, port))
    raise TdxProtocolError("no TDX host answered: " + ", ".join(errors[-8:]))


def sweep_sync(sections: Mapping[str, Callable[[TdxClient], Any]] | Iterable[tuple[str, Callable[[TdxClient], Any]]], *,
               host: tuple[str, int] | None = None, handshake_profile: str = "login_one",
               timeout_seconds: float = 5.0) -> dict[str, Any]:
    """Run independent sections on one selected host, with a new connection per section."""
    items = sections.items() if isinstance(sections, Mapping) else sections
    selected = host or _ordered_hosts(configured_hosts())[0]
    result: dict[str, Any] = {"host": f"{selected[0]}:{selected[1]}", "profile": handshake_profile, "sections": {}}
    for name, operation in items:
        try:
            value, receipt = call_sync(operation, hosts=[selected], handshake_profile=handshake_profile, timeout_seconds=timeout_seconds)
            result["sections"][name] = {"result": value, "rows": len(value) if hasattr(value, "__len__") else value, "receipt": receipt}
        except TdxProtocolError as error:
            result["sections"][name] = {"error": str(error)}
    return result


async def call(operation: Callable[[TdxClient], T], **kwargs: Any) -> tuple[T, str]:
    return await asyncio.to_thread(call_sync, operation, **kwargs)


def market_code(symbol: str) -> tuple[int, str]:
    """``"600519.SH"`` -> ``(1, "600519")``; an old BJ code is requested as its 920xxx code (tdx_bj_codes), so every
    TDX adapter that takes a symbol serves it; anything but six ASCII digits and a known exchange is a ValueError."""
    code, _, exchange = symbol.upper().partition(".")
    if exchange not in MARKETS or len(code) != 6 or not (code.isascii() and code.isdigit()):
        raise ValueError("symbol must be six digits followed by .SH, .SZ or .BJ")
    if exchange == "BJ":
        code = OLD_TO_NEW.get(code, code)
    return MARKETS[exchange], code


EXCHANGES = {market: exchange for exchange, market in MARKETS.items()}


def symbol(market: int, code: str) -> str:
    """The inverse of market_code: (0, "000001") -> "000001.SZ"."""
    return f"{code}.{EXCHANGES[market]}"


def observed_evidence(rows: list[dict[str, Any]], host: str, *, coverage: float | None = None,
                      warnings: tuple[str, ...] = ()) -> CapabilityEvidence:
    """Rows read just now from one host: available at collection time, the host label first."""
    observed = datetime.now(timezone.utc)
    return CapabilityEvidence(rows, coverage=coverage, available_at_min=observed, available_at_max=observed,
                              warnings=(f"tdx_host={host}", *warnings))


def requested_stocks(symbols: Sequence[str]) -> list[tuple[int, str]]:
    """The (market, code) pairs a batch adapter requests for ``symbols``; an empty request is a ValueError."""
    if not symbols:
        raise ValueError("symbols must not be empty")
    return [market_code(item) for item in symbols]


def batch_evidence(rows: list[dict[str, Any]], symbols: Sequence[str], host: str) -> CapabilityEvidence:
    """The rows a batch adapter read for ``symbols``. Coverage is the share of requested symbols that came back and the
    ones left out are named. The row of a symbol market_code translated (an old BJ code) carries source_symbol, the
    symbol as requested."""
    requested = [(item, symbol(*market_code(item))) for item in symbols]
    returned = {row["symbol"] for row in rows}
    missing = [item for item, answer in requested if answer not in returned]
    warnings = (f"missing_symbols={len(missing)}: {', '.join(missing[:10])}{' ...' if len(missing) > 10 else ''}",
                ) if missing else ()
    translated = {answer: item for item, answer in requested if item.upper() != answer}
    rows = [{**row, "source_symbol": translated[row["symbol"]]} if row["symbol"] in translated else row for row in rows]
    return observed_evidence(rows, host, coverage=len(rows) / len(symbols), warnings=warnings)


__all__ = [
    "BAR_CATEGORIES", "DEFAULT_HOSTS", "EXCHANGES", "HANDSHAKE_PROFILES", "MARKETS", "MINUTE_BAR_CATEGORIES", "PROVIDER_KEY", "TdxClient", "TdxProtocolError",
    "UPSTREAM_SITE", "XDXR_CATEGORIES", "batch_evidence", "build_bars_request", "build_history_ticks_request",
    "build_quotes_request", "build_ticks_request", "build_xdxr_request", "call", "call_sync",
    "configured_hosts", "decode_gbk", "decode_price", "decode_volume", "market_code", "observed_evidence", "parse_bars", "parse_quotes",
    "parse_ticks", "parse_xdxr", "requested_stocks", "sweep_sync", "symbol",
]
