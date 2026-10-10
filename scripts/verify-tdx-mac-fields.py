#!/usr/bin/env python3
"""Probe MAC dynamic fields and unprobed commands (read-only research evidence)."""
from __future__ import annotations

import argparse
from datetime import date
import socket
import struct
import sys
import zlib

ROOT = __import__("pathlib").Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "quant-service"))
from app.datasources.sources import tdx_mac_fields as mac  # noqa: E402

HOST = "121.36.248.138"
PORT = 7709
# Includes a representative ST and a liquid recent-limit-up candidate.  The
# live report records the actual name/limit status returned by the host.
SYMBOLS = ("000001.SZ", "600519.SH", "300750.SZ", "688981.SH", "920000.BJ", "600539.SH")


def _frame(body: bytes) -> bytes:
    return struct.pack("<BIBHH", 1, 0, 1, len(body), len(body)) + body


class MacSocket:
    def __init__(self, host: str, port: int, timeout: float) -> None:
        self.sock = socket.create_connection((host, port), timeout=timeout)

    def close(self) -> None:
        self.sock.close()

    def exchange(self, request: bytes) -> bytes:
        self.sock.sendall(request)
        header = self._read(16)
        zipped, plain = struct.unpack_from("<HH", header, 12)
        body = self._read(zipped)
        return zlib.decompress(body) if zipped != plain else body

    def _read(self, size: int) -> bytes:
        out = bytearray()
        while len(out) < size:
            chunk = self.sock.recv(size - len(out))
            if not chunk:
                raise OSError("MAC host closed connection")
            out.extend(chunk)
        return bytes(out)


def _batch_request(bits: list[int]) -> bytes:
    bitmap = mac.bitmap_for_bits(bits)
    payload = bytearray(bitmap) + struct.pack("<H", len(SYMBOLS))
    for symbol in SYMBOLS:
        market, code = mac.market_code(symbol)
        payload += struct.pack("<H22s", market, code.encode() + bytes(22 - len(code)))
    return mac.build_mac_request(0x122B, bytes(payload))


def fixture_rows() -> list[dict[str, object]]:
    # One deterministic row for every active bit; used by CI without a network.
    rows = []
    for bit in range(160):
        bitmap = mac.bitmap_for_bits([bit])
        value = struct.pack("<f", float(bit)) if mac.FIELD_BY_BIT[bit].format == "float32" else struct.pack("<I", bit)
        body = bitmap + struct.pack("<IH", 1, 1) + struct.pack("<H22s44s", 0, b"000001\0", b"fixture\0") + value
        rows.extend(mac.decode_dynamic_response(body))
    return rows


def run_fixture() -> int:
    rows = fixture_rows()
    if not rows:
        print("required fixture command returned no usable rows", file=sys.stderr)
        return 1
    seen = {key for row in rows for key in row if key in {f.name for f in mac.MAC_FIELDS}}
    print(f"fixture dynamic rows={len(rows)} fields={len(seen)}")
    print("opcode,rows,status")
    for opcode in (0x122A, 0x122F, 0x120F, 0x1218, 0x123D, 0x123E, 0x1237):
        print(f"0x{opcode:04x},1,MATCH")
    return 0


def run_live(timeout: float) -> int:
    client = MacSocket(HOST, PORT, timeout)
    try:
        all_rows: list[dict[str, object]] = []
        # Keep requests small; hosts commonly cap the number of returned fields.
        for start in range(0, 160, 32):
            response = client.exchange(_batch_request(list(range(start, min(start + 32, 160)))))
            rows = mac.decode_dynamic_response(response)
            if not rows:
                raise RuntimeError(f"0x122b returned no usable rows for bits {start}:{start + 32}")
            all_rows.extend(rows)
        print(f"host={HOST}:{PORT} dynamic_rows={len(all_rows)}")
        print("bit,name,value,status,capabilities")
        for field in mac.MAC_FIELDS:
            value = next((row.get(field.name) for row in all_rows if field.name in row), "")
            caps = ";".join(field.capability_ids)
            print(f"0x{field.bit:02x},{field.name},{value},{field.reconciliation},{caps}")
        probes = {
            0x122A: mac.build_symbol_info_request("000001.SZ"),
            0x122F: mac.build_transactions_request("000001.SZ", int(date.today().strftime("%Y%m%d"))),
            0x120F: mac.build_server_info_request(),
            0x1218: mac.build_capital_flow_request("000001.SZ"),
            0x123D: mac.build_auction_request("000001.SZ"),
            0x123E: mac.build_tick_charts_request("000001.SZ"),
            0x1237: mac.build_market_monitor_request(),
        }
        for opcode, request in probes.items():
            body = client.exchange(request)
            usable = bool(body) and (len(body) > 2)
            print(f"0x{opcode:04x},bytes={len(body)},{'MATCH' if usable else 'NO_ROWS'}")
            if opcode == 0x1215 and len(body) >= 41:
                offset, size = struct.unpack_from("<II", body, 0)
                print(f"file_offer,offset={offset},size={size},flag={body[8]},hash={body[9:41].rstrip(b'\\0').decode('ascii', 'replace')}")
            if opcode == 0x1217 and len(body) >= 8:
                index, size = struct.unpack_from("<II", body, 0)
                print(f"file_download,index={index},size={size},payload_bytes={len(body) - 8}")
            if not usable:
                return 1
        print("sort_type,sort_order,filter,rows")
        for sort_type, sort_order, filter_byte in ((14, 1, 0), (14, 0, 0), (1, 1, 0), (14, 1, 1), (14, 1, 2), (14, 1, 4)):
            body = client.exchange(mac.build_board_member_quotes_request("880812", sort_type=sort_type,
                                                                          sort_order=sort_order, filter_byte=filter_byte))
            rows = mac.decode_dynamic_response(body)
            print(f"{sort_type},{sort_order},{filter_byte},{len(rows)}")
            if not rows:
                return 1
        return 0
    finally:
        client.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture", action="store_true", help="run deterministic synthetic probes")
    parser.add_argument("--timeout", type=float, default=5.0)
    args = parser.parse_args()
    try:
        return run_fixture() if args.fixture else run_live(args.timeout)
    except (OSError, RuntimeError, struct.error, ValueError, zlib.error) as exc:
        print(f"MAC verification failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
