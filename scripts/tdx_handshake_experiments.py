#!/usr/bin/env python3
"""Read-only TDX handshake/opcode probe.

This intentionally does not modify ``tdx_protocol.py``.  It sends small market
data requests to a bounded host list and prints wire evidence for each variant.
Run from the repository root; the quant-service source tree is added to
``sys.path`` automatically.
"""

from __future__ import annotations

import argparse
import socket
import struct
import sys
import zlib
from pathlib import Path
from typing import Callable


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "quant-service"))
from app.datasources.sources import tdx_protocol as tdx  # noqa: E402


LOGIN_TWO_PAYLOAD = bytes.fromhex(
    "d5 d0 c9 cc d6 a4 a8 af 00 00 00 8f c2 25 40 13 00 00"
    "d5 00 c9 cc bd f0 d7 ea 00 00 00 02"
)


def std_frame(seq: int, packet_type: int, method: int, payload: bytes) -> bytes:
    """Go/modern header: 0c | uint32 seq | packet | lengths | method | data."""
    length = 2 + len(payload)
    return struct.pack("<BI BHHH", 0x0C, seq, packet_type, length, length, method) + payload


def legacy_frame(request: bytes) -> bytes:
    return request


def setup_commands(kind: str) -> tuple[bytes, ...]:
    if kind == "legacy_3":
        return tdx._SETUP_COMMANDS
    hello1 = std_frame(0x021893, 1, 0x000D, b"\x01")
    hello1_again = std_frame(0x021894, 1, 0x000D, b"\x02")
    hello2 = std_frame(0x031899, 1, 0x0FDB, LOGIN_TWO_PAYLOAD)
    if kind == "go_2":
        return hello1, hello2
    if kind == "go_3":
        return hello1, hello1_again, hello2
    if kind == "login_one_only":
        return hello1,
    raise ValueError(kind)


def quote_payload() -> bytes:
    return struct.pack("<H", 1) + bytes([0]) + b"000001"


def quote_payload_old() -> bytes:
    return bytes.fromhex("05 00 00 00 00 00 00 00") + quote_payload()


def quote_payload_encrypt() -> bytes:
    return struct.pack("<H", 1) + bytes([0]) + b"000001" + struct.pack("<HH", 22234, 2)


def bars_payload(category: int, *, start: int = 0, count: int = 8) -> bytes:
    return struct.pack(
        "<H6sHHHHIIH", 0, b"000001", category, 1, start, count, 0, 0, 0
    )


def minute_payload(*, start: int = 0, count: int = 8) -> bytes:
    return struct.pack("<H6sHH", 0, b"000001", start, count)


def minute_history_payload() -> bytes:
    return struct.pack("<iB6s", -20261008, 0, b"000001")


def request_variants() -> tuple[tuple[str, Callable[[int], bytes], str], ...]:
    def legacy_quote(_seq: int) -> bytes:
        return legacy_frame(tdx.build_quotes_request([(0, "000001")]))

    def modern_quote(seq: int) -> bytes:
        return std_frame(seq, 0, 0x054C, quote_payload_old())

    def security_quote(seq: int) -> bytes:
        return std_frame(seq, 1, 0x053E, quote_payload_old())

    def encrypted_quote(seq: int) -> bytes:
        return std_frame(seq, 1, 0x0547, quote_payload_encrypt())

    def legacy_day(_seq: int) -> bytes:
        return legacy_frame(tdx.build_bars_request(9, 0, "000001", 0, 8))

    def std_day(seq: int) -> bytes:
        return std_frame(seq, 0, 0x052D, bars_payload(9))

    def modern_day(seq: int) -> bytes:
        return std_frame(seq, 0, 0x0523, bars_payload(9))

    def one_minute_bars(seq: int) -> bytes:
        return std_frame(seq, 0, 0x052D, bars_payload(8))

    def minute(seq: int) -> bytes:
        return std_frame(seq, 0, 0x0537, minute_payload())

    def minute_old(seq: int) -> bytes:
        return std_frame(seq, 0, 0x0FB4, minute_history_payload())

    def auction(seq: int) -> bytes:
        return std_frame(seq, 0, 0x056A, bars_payload(8))

    return (
        ("quote_legacy_053e", legacy_quote, "quote"),
        ("quote_std_053e", security_quote, "quote"),
        ("quote_batch_054c", modern_quote, "quote"),
        ("quote_encrypted_0547", encrypted_quote, "encrypted_quote"),
        ("bars_legacy_052d", legacy_day, "bars"),
        ("bars_std_052d", std_day, "bars"),
        ("bars_modern_0523", modern_day, "bars"),
        ("bars_1m_052d", one_minute_bars, "bars_1m"),
        ("bars_minute_0537", minute, "minute"),
        ("bars_minute_old_0fb4", minute_old, "minute_old"),
        ("auction_056a", auction, "bars"),
    )


def recv_exact(sock: socket.socket, size: int) -> bytes:
    out = bytearray()
    while len(out) < size:
        chunk = sock.recv(size - len(out))
        if not chunk:
            raise ConnectionError("server closed connection")
        out.extend(chunk)
    return bytes(out)


def exchange(sock: socket.socket, request: bytes) -> bytes:
    sock.sendall(request)
    header = recv_exact(sock, 16)
    # The wire response is the same 16-byte envelope used by tdx_protocol.py.
    _prefix, _control, _seq, zipped, plain = struct.unpack("<IIIHH", header)
    body = recv_exact(sock, zipped)
    if zipped != plain:
        body = zlib.decompress(body)
    return body


def skip_price(data: bytes, pos: int) -> int:
    while True:
        if pos >= len(data):
            raise ValueError("truncated varint")
        value = data[pos]
        pos += 1
        if value & 0x80 == 0:
            return pos


def parse_minute_count(body: bytes, *, history: bool) -> int:
    if len(body) < (10 if history else 4):
        raise ValueError("short minute body")
    count = struct.unpack_from("<H", body)[0]
    pos = 10 if history else 4
    for _ in range(count):
        for _field in range(3):
            pos = skip_price(body, pos)
    return count


def parse_encrypted_count(body: bytes) -> int:
    decoded = bytes(value ^ 0x93 for value in body)
    if len(decoded) < 2:
        raise ValueError("short encrypted body")
    count = struct.unpack_from("<H", decoded)[0]
    pos = 2
    for _ in range(count):
        if pos + 9 > len(decoded):
            raise ValueError("truncated encrypted quote header")
        pos += 9
        for _field in range(5):
            pos = skip_price(decoded, pos)
        pos += 4
        for _field in range(3):
            pos = skip_price(decoded, pos)
        pos += 4
        for _field in range(4 + 5 * 4):
            pos = skip_price(decoded, pos)
        pos += 10
        for _field in range(6 * 4):
            pos = skip_price(decoded, pos)
    if pos > len(decoded):
        raise ValueError("truncated encrypted quote tail")
    return count


def row_count(body: bytes, kind: str) -> tuple[int | None, str]:
    if len(body) == 2:
        return None, f"two-byte-error={body.hex()}"
    try:
        if kind == "encrypted_quote":
            return parse_encrypted_count(body), "xor93-parser"
        if kind == "quote":
            return len(tdx.parse_quotes(body)), "quote-parser"
        if kind == "bars":
            return len(tdx.parse_bars(9, body)), "bar-parser"
        if kind == "bars_1m":
            return len(tdx.parse_bars(8, body)), "1m-bar-parser"
        if kind == "minute":
            return parse_minute_count(body, history=False), "minute-parser"
        if kind == "minute_old":
            return parse_minute_count(body, history=True), "history-minute-parser"
    except (IndexError, struct.error, ValueError) as exc:
        return None, f"parse-error={type(exc).__name__}:{exc}"
    return None, "unknown-parser"


def probe(host: str, port: int, handshake: str, limit: int) -> None:
    print(f"HOST {host}:{port} handshake={handshake}")
    for index, (name, builder, kind) in enumerate(request_variants()[:limit]):
        try:
            # Reconnect for every request: a timeout or server close must not
            # poison the next opcode's response framing.
            with socket.create_connection((host, port), timeout=5.0) as sock:
                sock.settimeout(5.0)
                setup_bodies = []
                for command in setup_commands(handshake):
                    setup_bodies.append(exchange(sock, command))
                if index == 0:
                    for body in setup_bodies:
                        print(f"  setup len={len(body)} first={body[:16].hex()}")
                seq = 0x70000000 + index
                body = exchange(sock, builder(seq))
                count, note = row_count(body, kind)
                print(f"  {name}: raw_len={len(body)} first={body[:16].hex()} parsed_rows={count} ({note})")
        except Exception as exc:  # probe must continue across command variants
            print(f"  {name}: ERROR {type(exc).__name__}: {exc}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hosts", type=int, default=4, help="number of DEFAULT_HOSTS to probe")
    parser.add_argument("--handshakes", nargs="+", default=["legacy_3", "go_2", "go_3", "login_one_only"])
    parser.add_argument("--requests", type=int, default=99, help="maximum request variants per connection")
    args = parser.parse_args()
    hosts = tdx.DEFAULT_HOSTS[: max(1, min(args.hosts, len(tdx.DEFAULT_HOSTS)))]
    for host, port in hosts:
        for handshake in args.handshakes:
            probe(host, port, handshake, args.requests)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
