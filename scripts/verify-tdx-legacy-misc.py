#!/usr/bin/env python3
"""Probe the supported legacy TDX ranking and index-overview commands."""

from __future__ import annotations

import argparse
import json
import socket
import struct
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "quant-service"))

from app.datasources.sources import tdx_legacy_misc as legacy  # noqa: E402
from app.datasources.sources import tdx_protocol  # noqa: E402

COMMAND_ERRORS = (OSError, socket.timeout, tdx_protocol.TdxProtocolError, ValueError, IndexError, struct.error)


def _run(result: dict, name: str, fn):
    started = time.perf_counter()
    try:
        value = fn()
    except COMMAND_ERRORS as exc:
        result["commands"][name] = {"ok": False, "error": type(exc).__name__}
        return None
    result["commands"][name] = {"ok": True, "elapsed_ms": round((time.perf_counter() - started) * 1000, 2)}
    return value


def _probe_host(host: str, port: int, timeout: float) -> dict:
    result: dict = {"host": f"{host}:{port}", "commands": {}}
    try:
        with tdx_protocol.TdxClient(host, port, timeout, handshake_profile="login_one") as client:
            # Run the adapter's own sweep (finding 9)
            rows, warnings_dict = _run(result, "0x054b", lambda: legacy._all_a_snapshot(client))
            if rows:
                result["0x054b_row_count"] = len(rows)
                result["0x054b_warnings"] = warnings_dict
            # Index overview with R1 echo check (finding 1)
            index = _run(result, "0x051d", lambda: legacy.parse_index_info(
                client._exchange(legacy.build_index_info_request(1, "999999")),
                request_market=1, request_code="999999"))
            if index:
                result["index_fields"] = sorted(index)
            # Heartbeat with packet type 1 (finding 9)
            heartbeat = _run(result, "0x0004", lambda: client._exchange(legacy._header(legacy.KMSG_INDEXINFO, packet_type=1)))
            result["usable"] = bool(rows) and bool(index) and heartbeat is not None
    except COMMAND_ERRORS as exc:
        result["error"] = type(exc).__name__
        result["usable"] = False
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--timeout", type=float, default=5.0)
    args = parser.parse_args()
    # Use generated host pool (finding 9)
    hosts = tdx_protocol.DEFAULT_HOSTS
    reports = [_probe_host(host, port, args.timeout) for host, port in hosts]
    for report in reports:
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if any(report.get("usable") for report in reports) else 1


if __name__ == "__main__":
    raise SystemExit(main())
