#!/usr/bin/env python3
"""Probe TDX extended market (7727) codes, metadata, quotes and bars.

The probe is bounded to four supplied hosts, one connection per host, three
100-row instrument pages, and a small read-only symbol set.  It exits non-zero
only when no host completes the setup/login/count/categories exchange.
"""

from __future__ import annotations

import json
import socket
import struct
import sys
import zlib
from datetime import datetime

HOSTS = (("113.45.175.47", 7727), ("150.158.20.127", 7727),
         ("139.9.191.175", 7727), ("106.52.170.195", 7727))
METHOD = {"login": 0x2454, "count": 0x23F0, "categories": 0x23F4,
          "instruments": 0x23F5, "quote": 0x23FA, "kline": 0x23FF}
LOGIN = bytes.fromhex(
    "e5bb1c2fafe525941f32c6e5d53dfb415b734cc9cdbf0ac92021bfdd1eb06d22"
    "d008884c1611cb1378f6abd824d899d21f32c6e5d53dfb411f32c6e5d53dfb41"
    "a9325ac935dc0837335a16e4ce17c1bb")
SETUP = bytes.fromhex("1f32c6e5d53dfb41" * 8 + "cce16dffd5ba3fb8cbc57a054f7748ea")
MARKETS = (12, 47, 60, 30, 29, 28, 31, 48, 74, 10, 11, 16, 17, 18, 38, 46, 62, 69, 70, 75)
QUOTES = ((31, "00700"), (74, "AAPL"), (47, "IF2610"), (47, "IFL0"),
          (47, "IFL1"), (47, "IH2610"), (47, "IC2610"), (47, "IM2610"),
          (47, "T2612"), (47, "TF2612"), (47, "TS2612"), (47, "TL2612"),
          (30, "AU2610"), (30, "AU2612"), (10, "USDCNH"), (10, "USDCNY"),
          (12, "HSI"), (27, "HSI"), (27, "HSTECH"), (27, "SPX"),
          (27, "DJI"), (27, "IXIC"), (27, "N225"), (27, "A50"))


def text(raw: bytes) -> str:
    return raw.split(b"\0", 1)[0].decode("gbk", "replace").strip()


def frame(method: int, payload: bytes = b"") -> bytes:
    body = struct.pack("<H", method) + payload
    return struct.pack("<BIBHH", 1, 0, 1, len(body), len(body)) + body


def setup() -> bytes:
    return bytes.fromhex("01 01 48 65 00 01 52 00 52 00") + struct.pack("<H", METHOD["login"]) + SETUP


def code(value: str, size: int = 9) -> bytes:
    raw = value.encode("ascii")
    if len(raw) > size:
        raise ValueError(value)
    return raw.ljust(size, b"\0")


def categories(body: bytes) -> list[dict[str, object]]:
    count = struct.unpack_from("<H", body)[0]
    rows = []
    for i in range(count):
        p = 2 + i * 64
        rows.append({"market": body[p], "name": text(body[p + 1:p + 33]), "goods_type": body[p + 33], "abbr": text(body[p + 34:p + 36])})
    return rows


def instruments(body: bytes) -> list[dict[str, object]]:
    count = struct.unpack_from("<H", body, 4)[0]
    rows = []
    for i in range(count):
        p = 6 + i * 64
        rows.append({"category": body[p], "market": body[p + 1], "code": text(body[p + 5:p + 14]), "name": text(body[p + 14:p + 31])})
    return rows


def quote(body: bytes) -> dict[str, object]:
    market, symbol = body[0], text(body[1:10])
    p = 14
    pre, op, high, low, price = struct.unpack_from("<5f", body, p)
    open_interest = struct.unpack_from("<I", body, p + 20)[0]
    volume = struct.unpack_from("<I", body, p + 28)[0]
    return {"market": market, "code": symbol, "price": round(price, 6), "pre_close": round(pre, 6),
            "open": round(op, 6), "high": round(high, 6), "low": round(low, 6), "volume": volume,
            "open_interest": open_interest}


def klines(body: bytes, category: int) -> list[dict[str, object]]:
    count = struct.unpack_from("<H", body, 18)[0]
    rows = []
    for i in range(count):
        p = 20 + i * 32
        raw = body[p:p + 4]
        if category < 4 or category in (7, 8):
            day, minutes = struct.unpack("<HH", raw)
            stamp = f"{(day >> 11) + 2004:04d}-{(day & 2047) // 100:02d}-{(day & 2047) % 100:02d} {minutes // 60:02d}:{minutes % 60:02d}"
        else:
            value = struct.unpack("<I", raw)[0]
            stamp = f"{value // 10000:04d}-{value % 10000 // 100:02d}-{value % 100:02d}"
        op, high, low, close, settlement = struct.unpack_from("<5f", body, p + 4)
        position, volume = struct.unpack_from("<II", body, p + 24)
        rows.append({"datetime": stamp, "open": round(op, 6), "high": round(high, 6), "low": round(low, 6),
                     "close": round(close, 6), "settlement": round(settlement, 6), "position": position, "volume": volume})
    return rows


class Client:
    def __init__(self, host: str, port: int) -> None:
        self.sock = socket.create_connection((host, port), timeout=5)

    def exchange(self, request: bytes) -> bytes:
        self.sock.sendall(request)
        header = self.sock.recv(16)
        while len(header) < 16:
            header += self.sock.recv(16 - len(header))
        zipped, plain = struct.unpack_from("<HH", header, 12)
        body = b""
        while len(body) < zipped:
            body += self.sock.recv(zipped - len(body))
        return zlib.decompress(body) if zipped != plain else body

    def request(self, method: str, payload: bytes = b"") -> bytes:
        return self.exchange(frame(METHOD[method], payload))

    def close(self) -> None:
        self.sock.close()


def run_host(host: str, port: int) -> dict[str, object]:
    client = Client(host, port)
    try:
        client.exchange(setup())
        login = client.request("login")
        result: dict[str, object] = {"host": host, "server_name": text(login[61:82]), "description": text(login[93:244]),
                                     "count": struct.unpack_from("<I", client.request("count"), 19)[0]}
        result["categories"] = categories(client.request("categories"))
        page_rows = []
        for start in (0, 100, 200):
            page_rows.extend(instruments(client.request("instruments", struct.pack("<IH", start, 100))))
        result["instrument_pages"] = {str(m): [row for row in page_rows if row["market"] == m] for m in MARKETS}
        result["quotes"] = {}
        for market, symbol in QUOTES:
            body = client.request("quote", struct.pack("<B9s", market, code(symbol)))
            result["quotes"][f"{market}:{symbol}"] = quote(body)
        result["bars"] = {}
        for category, market, symbol in ((9, 31, "00700"), (9, 74, "AAPL"), (4, 47, "IF2610"), (4, 30, "AU2610")):
            body = client.request("kline", struct.pack("<B9sHHIH", market, code(symbol), category, 1, 0, 5))
            result["bars"][f"{market}:{symbol}"] = klines(body, category)
        return result
    finally:
        client.close()


def main() -> int:
    results = []
    for host, port in HOSTS:
        try:
            results.append(run_host(host, port))
        except (OSError, TimeoutError, struct.error, zlib.error, ValueError) as error:
            results.append({"host": host, "error": f"{type(error).__name__}: {error}"})
    print(json.dumps({"observed_at": datetime.now().astimezone().isoformat(), "hosts": results}, ensure_ascii=False, indent=2))
    return 0 if any("error" not in result for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
