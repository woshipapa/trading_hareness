#!/usr/bin/env python3
"""Read-only live evidence probe for TDX volume, amount and time units."""
from __future__ import annotations

import json
import socket
import struct
import sys
import zlib
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "quant-service"))
from app.datasources.sources import tdx_protocol

LEGACY_HOSTS = [("60.191.117.167", 7709), ("218.75.126.9", 7709), ("117.34.114.13", 7709)]
MAC_HOST = ("121.36.248.138", 7709)
SESSION = date(2026, 10, 9)
SYMBOLS = [(0, "000001"), (1, "600519"), (0, "300750"), (1, "688981"), (1, "510300")]


def _read(sock: socket.socket, size: int) -> bytes:
    out = bytearray()
    while len(out) < size:
        chunk = sock.recv(size - len(out))
        if not chunk:
            raise OSError("server closed connection")
        out.extend(chunk)
    return bytes(out)


def _exchange(sock: socket.socket, request: bytes) -> bytes:
    sock.sendall(request)
    header = _read(sock, 16)
    zipped, plain = struct.unpack_from("<HH", header, 12)
    body = _read(sock, zipped)
    return zlib.decompress(body) if zipped != plain else body


class LoginOne(tdx_protocol.TdxClient):
    def __enter__(self):
        self._socket = socket.create_connection((self.host, self.port), timeout=self.timeout)
        self._exchange(tdx_protocol._SETUP_COMMANDS[0])
        return self


def decode_legacy_bar(category: int, body: bytes) -> list[dict[str, float | str]]:
    """Decode 0x052d: prices are delta-varints; volume/amount are uint32."""
    count = struct.unpack_from("<H", body)[0]
    pos, base, rows = 2, 0, []
    for _ in range(count):
        stamp, pos = tdx_protocol._bar_datetime(category, body, pos)
        opens, pos = tdx_protocol.decode_price(body, pos)
        closes, pos = tdx_protocol.decode_price(body, pos)
        highs, pos = tdx_protocol.decode_price(body, pos)
        lows, pos = tdx_protocol.decode_price(body, pos)
        volume, amount = struct.unpack_from("<ff", body, pos)
        pos += 8
        opening = base + opens
        rows.append({"datetime": stamp, "open": opening / 1000, "close": (opening + closes) / 1000,
                     "high": (opening + highs) / 1000, "low": (opening + lows) / 1000,
                     "volume_shares": 0.0 if 0 < volume < 1e-20 else float(volume),
                     "amount_yuan": 0.0 if 0 < amount < 1e-20 else float(amount)})
        base = opening + closes
    return rows


def mac_request(opcode: int, payload: bytes) -> bytes:
    body = struct.pack("<H", opcode) + payload
    return struct.pack("<BIBHH", 1, 0, 1, len(body), len(body)) + body


def mac_bars_request(market: int, code: str) -> bytes:
    payload = struct.pack("<H22sHHIHHbbbbH4s", market, code.encode().ljust(22, b"\0"), 8, 1, 0, 241, 0, 1, 1, 0, 1, 0, b"")
    return mac_request(0x122E, payload)


def mac_bars(body: bytes) -> list[dict[str, float | int]]:
    count = struct.unpack_from("<H", body, 27)[0]
    return [{"seconds": struct.unpack_from("<I", body, 33 + i * 36 + 4)[0],
             "volume_shares": struct.unpack_from("<f", body, 33 + i * 36 + 28)[0]}
            for i in range(count)]


def _legacy_raw_categories(client: LoginOne, market: int, code: str) -> dict[str, object]:
    out = {}
    for label, category in (("1m", 8), ("5m", 0), ("15m", 1), ("30m", 2), ("60m", 3)):
        body = client._exchange(tdx_protocol.build_bars_request(category, market, code, 0, 240))
        rows = decode_legacy_bar(category, body)
        out[label] = {"rows": len(rows), "first": rows[0] if rows else None, "last": rows[-1] if rows else None}
    return out


def main() -> int:
    result: dict[str, object] = {"session": SESSION.isoformat(), "symbols": {}}
    failures = 0
    host = LEGACY_HOSTS[0]
    with LoginOne(*host, timeout_seconds=5) as client:
        for market, code in SYMBOLS:
            categories = _legacy_raw_categories(client, market, code)
            body = client._exchange(tdx_protocol.build_bars_request(8, market, code, 0, 240))
            rows = decode_legacy_bar(8, body)
            legacy_volumes = [float(row["volume_shares"]) / 100 for row in rows]
            bad = len(rows) != 240
            result["symbols"][f"{code}.{('SZ' if market == 0 else 'SH')}"] = {
                "legacy_rows": len(rows), "legacy_total_lots": round(sum(row["volume_shares"] for row in rows) / 100),
                "legacy_zero_denorm": sum(0 < row["volume_shares"] < 1e-20 for row in rows), "decode_failed": bad,
                "legacy_categories": categories,
                "legacy_minute_lots": legacy_volumes,
            }
            failures += int(bad)
    with socket.create_connection(MAC_HOST, timeout=5) as sock:
        for packet in (bytes.fromhex("0c 02 18 93 00 01 03 00 03 00 0d 00 01"), bytes.fromhex("0c 02 18 94 00 01 03 00 03 00 0d 00 02")):
            _exchange(sock, packet)
        for market, code in SYMBOLS:
            body = _exchange(sock, mac_bars_request(market, code))
            rows = mac_bars(body)
            key = f"{code}.{('SZ' if market == 0 else 'SH')}"
            legacy = result["symbols"][key]
            mac_volumes = [float(row["volume_shares"]) / 100 for row in rows[1:]]
            legacy["mac_rows_including_sentinel"] = len(rows)
            legacy["mac_total_lots"] = round(sum(mac_volumes))
            legacy["minute_mismatches_after_scale"] = sum(
                abs(left - right) > 1 for left, right in zip(legacy["legacy_minute_lots"], mac_volumes))
            legacy["mac_legacy_1lot_equal"] = len(rows) == 241 and abs(legacy["legacy_total_lots"] - legacy["mac_total_lots"]) <= 1
            del legacy["legacy_minute_lots"]
            failures += int(not legacy["mac_legacy_1lot_equal"])
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
