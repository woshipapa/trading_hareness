#!/usr/bin/env python3
"""Probe TDX stock capital-flow fields against ticks and public references."""
from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path
import socket
import struct
import subprocess
import sys
import urllib.parse
import zlib

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "quant-service"))
from app.datasources.sources import tdx_protocol

MAC_HOST = ("121.36.248.138", 7709)
LEGACY_HOST = ("117.34.114.13", 7709)
SESSION = date(2026, 10, 9)
SYMBOLS = ("000001.SZ", "600519.SH", "300750.SZ", "688981.SH", "920000.BJ",
           "300530.SZ", "600812.SH", "300821.SZ")
FIELDS = {
    56: "main_net_amount", 57: "bid_ask_ratio", 107: "main_net_amount_copy",
    108: "field_0x6c", 109: "field_0x6d", 110: "field_0x6e",
    111: "field_0x6f", 112: "field_0x70", 113: "field_0x71",
    114: "field_0x72", 115: "ddx", 116: "ddy", 117: "ddz", 118: "ddf",
}


def mac_request(opcode: int, payload: bytes, *, head: int = 1) -> bytes:
    body = struct.pack("<H", opcode) + payload
    return struct.pack("<BIBHH", head, 0, 1, len(body), len(body)) + body


def bitmap(bits: tuple[int, ...]) -> bytes:
    value = bytearray(20)
    for bit in bits:
        value[bit // 8] |= 1 << (bit % 8)
    return bytes(value)


def market_code(symbol: str) -> tuple[int, str]:
    code, exchange = symbol.split(".")
    return {"SZ": 0, "SH": 1, "BJ": 2}[exchange], code


class MacClient:
    def __enter__(self) -> "MacClient":
        self.socket = socket.create_connection(MAC_HOST, timeout=5)
        return self

    def __exit__(self, *_exc: object) -> None:
        self.socket.close()

    def exchange(self, request: bytes) -> bytes:
        self.socket.sendall(request)
        header = self.read(16)
        zipped, plain = struct.unpack_from("<HH", header, 12)
        body = self.read(zipped)
        return zlib.decompress(body) if zipped != plain else body

    def read(self, size: int) -> bytes:
        data = bytearray()
        while len(data) < size:
            chunk = self.socket.recv(size - len(data))
            if not chunk:
                raise OSError("MAC host closed the connection")
            data.extend(chunk)
        return bytes(data)


class LoginOneClient(tdx_protocol.TdxClient):
    def __enter__(self) -> "LoginOneClient":
        self._socket = socket.create_connection((self.host, self.port), timeout=self.timeout)
        self._exchange(tdx_protocol._SETUP_COMMANDS[0])
        return self


def batch_request() -> bytes:
    bits = tuple(FIELDS)
    payload = bytearray(bitmap(bits)) + struct.pack("<H", len(SYMBOLS))
    for symbol in SYMBOLS:
        market, code = market_code(symbol)
        payload += struct.pack("<H22s", market, code.encode().ljust(22, b"\0"))
    return mac_request(0x122B, bytes(payload))


def decode_batch(body: bytes) -> list[dict[str, object]]:
    active = tuple(FIELDS)
    count = struct.unpack_from("<H", body, 24)[0]
    row_size = 68 + len(active) * 4
    rows = []
    for index in range(count):
        start = 26 + index * row_size
        row = {"symbol": body[start + 2:start + 24].split(b"\0", 1)[0].decode()}
        for offset, bit in enumerate(active):
            row[FIELDS[bit]] = struct.unpack_from("<f", body, start + 68 + offset * 4)[0]
        rows.append(row)
    return rows


def flow_request(symbol: str) -> bytes:
    market, code = market_code(symbol)
    payload = struct.pack("<H8s16s21s", market, code.encode().ljust(8, b"\0"), b"", b"Stock_ZJLX".ljust(21, b"\0"))
    return mac_request(0x1218, payload, head=2)


def tick_stats(rows: list[dict[str, object]]) -> dict[str, float | int]:
    trades = [row for row in rows if row["side"] in ("B", "S", "N") and row["volume_lots"]]
    out: dict[str, float | int] = {"ticks": len(rows)}
    for threshold in (0, 200_000, 500_000, 1_000_000):
        out[f"side_net_ge_{threshold}_yuan"] = round(sum(
            (1 if row["side"] == "B" else -1 if row["side"] == "S" else 0)
            * float(row["price"]) * int(row["volume_lots"]) * 100
            for row in trades if float(row["price"]) * int(row["volume_lots"]) * 100 >= threshold), 2)
    previous = None
    direction = 0
    tick_net = 0.0
    for row in trades:
        price = float(row["price"])
        direction = 1 if previous is not None and price > previous else -1 if previous is not None and price < previous else direction
        tick_net += direction * price * int(row["volume_lots"]) * 100
        previous = price
    out["price_change_tick_rule_yuan"] = round(tick_net, 2)
    return out


def eastmoney() -> list[dict[str, object]]:
    rows = []
    for start in range(0, len(SYMBOLS), 4):
        symbols = SYMBOLS[start:start + 4]
        secids = ",".join(f"{1 if symbol.endswith('.SH') else 0}.{symbol[:6]}" for symbol in symbols)
        query = urllib.parse.urlencode({"fltt": "2", "invt": "2", "fields": "f12,f14,f62,f184", "secids": secids})
        response = subprocess.run(["curl", "-fsS", "--max-time", "5", "-A", "Mozilla/5.0",
                                   "https://push2.eastmoney.com/api/qt/ulist.np/get?" + query],
                                  capture_output=True, text=True, check=True)
        rows.extend(json.loads(response.stdout)["data"]["diff"])
    return rows


def run() -> dict[str, object]:
    with MacClient() as client:
        fields = decode_batch(client.exchange(batch_request()))
        flows = {symbol: json.loads(client.exchange(flow_request(symbol))[27:].decode("gbk")) for symbol in SYMBOLS}
    ticks = {}
    with LoginOneClient(*LEGACY_HOST, timeout_seconds=5) as client:
        for symbol in SYMBOLS:
            ticks[symbol] = tick_stats(client.ticks(*market_code(symbol), SESSION, max_requests=20))
    return {"session": SESSION.isoformat(), "mac_host": f"{MAC_HOST[0]}:{MAC_HOST[1]}",
            "legacy_host": f"{LEGACY_HOST[0]}:{LEGACY_HOST[1]}", "fields": fields,
            "flow_0x1218": flows, "ticks": ticks, "eastmoney": eastmoney()}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture", action="store_true")
    args = parser.parse_args()
    result = ({"fields": decode_batch(bitmap(tuple(FIELDS)) + struct.pack("<IH", 1, 1)
              + struct.pack("<H22s44s", 0, b"000001", b"fixture") + b"\0" * (len(FIELDS) * 4))}
              if args.fixture else run())
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["fields"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
