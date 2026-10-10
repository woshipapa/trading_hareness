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

HOSTS = (("60.191.117.167", 7709), ("218.75.126.9", 7709), ("117.34.114.13", 7709))
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


def _ranking(client: tdx_protocol.TdxClient) -> list[dict]:
    rows: list[dict] = []
    for start in range(0, 80 * 100, 80):
        page = legacy.parse_quotes_list(client._exchange(
            legacy.build_quotes_list_request(legacy.QUOTE_CATEGORIES["all_a"],
                                             legacy.QUOTE_SORT_TYPES["change_pct"], start, 80, True)))
        rows.extend(page)
        if len(page) < legacy.RANKING_PAGE_SIZE:
            return rows
    return rows


def _probe_host(host: str, port: int, timeout: float) -> dict:
    result: dict = {"host": f"{host}:{port}", "commands": {}}
    try:
        with tdx_protocol.TdxClient(host, port, timeout, handshake_profile="login_one") as client:
            rows = _run(result, "0x054b", lambda: _ranking(client))
            index = _run(result, "0x051d", lambda: legacy.parse_index_info(
                client._exchange(legacy.build_index_info_request(1, "000001"))))
            momentum = _run(result, "0x051c", lambda: legacy.parse_index_momentum(
                client._exchange(legacy.build_index_momentum_request(1, "000001"))))
            ping = _run(result, "0x0015", lambda: client._exchange(legacy._header(legacy.KMSG_PING, packet_type=0)))
            heartbeat = _run(result, "0x0004", lambda: client._exchange(legacy._header(legacy.KMSG_HEARTBEAT)))
            result["usable"] = bool(rows) and bool(index) and momentum is not None and ping is not None and heartbeat is not None
            if index:
                result["index_fields"] = sorted(index)
    except COMMAND_ERRORS as exc:
        result["error"] = type(exc).__name__
        result["usable"] = False
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--timeout", type=float, default=5.0)
    args = parser.parse_args()
    reports = [_probe_host(host, port, args.timeout) for host, port in HOSTS]
    for report in reports:
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if any(report.get("usable") for report in reports) else 1


if __name__ == "__main__":
    raise SystemExit(main())
