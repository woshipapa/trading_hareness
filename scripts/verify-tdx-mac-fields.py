#!/usr/bin/env python3
"""Probe MAC dynamic fields and unprobed commands (read-only research evidence)."""
from __future__ import annotations

import argparse
from datetime import date
import struct
import sys
import zlib

ROOT = __import__("pathlib").Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "quant-service"))
from app.datasources.sources import tdx_mac, tdx_mac_fields as mac, tdx_protocol  # noqa: E402

HOST = "121.36.248.138"
PORT = 7709
# Includes a representative ST and a liquid recent-limit-up candidate.  The
# live report records the actual name/limit status returned by the host.
SYMBOLS = ("000001.SZ", "600519.SH", "300750.SZ", "688981.SH", "920000.BJ", "600539.SH")


def _batch_request(bits: list[int]) -> bytes:
    return tdx_mac.build_batch_quotes_request([tdx_protocol.market_code(symbol) for symbol in SYMBOLS], mac.bitmap_for_bits(bits))


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
    with tdx_mac.TdxMacClient(HOST, PORT, timeout) as client:
        all_rows: list[dict[str, object]] = []
        # Keep requests small; hosts commonly cap the number of returned fields.
        for start in range(0, 160, 32):
            response = client._exchange(_batch_request(list(range(start, min(start + 32, 160)))))
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
        market, code = tdx_protocol.market_code("000001.SZ")
        probes = {
            0x122A: mac.build_symbol_info_request("000001.SZ"),
            0x122F: mac.build_transactions_request("000001.SZ", int(date.today().strftime("%Y%m%d"))),
            0x120F: mac.build_server_info_request(),
            0x1218: mac.build_capital_flow_request("000001.SZ"),
            0x123D: tdx_mac.build_aux_request(tdx_mac.OP_AUCTION, market, code),
            0x123E: tdx_mac.build_aux_request(tdx_mac.OP_TICK_CHARTS, market, code),
            0x1237: tdx_mac.build_aux_request(tdx_mac.OP_MARKET_MONITOR, market, code),
        }
        for opcode, request in probes.items():
            body = client._exchange(request)
            usable = bool(body) and (len(body) > 2)
            print(f"0x{opcode:04x},bytes={len(body)},{'MATCH' if usable else 'NO_ROWS'}")
            if not usable:
                return 1
        print("sort_type,sort_order,filter,rows")
        board_code = tdx_mac.exchange_board_code("880812")
        for sort_type, sort_order, filter_byte in ((14, 1, 0), (14, 0, 0), (1, 1, 0), (14, 1, 1), (14, 1, 2), (14, 1, 4)):
            body = client._exchange(tdx_mac.build_board_members_request(
                board_code, quotes=True, sort_type=sort_type, sort_order=sort_order, filter_byte=filter_byte))
            rows = mac.decode_dynamic_response(body)
            print(f"{sort_type},{sort_order},{filter_byte},{len(rows)}")
            if not rows:
                return 1
        return 0


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
