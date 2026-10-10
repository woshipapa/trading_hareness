#!/usr/bin/env python3
"""Probe TDX breadth, MAC dead commands, offset info and chart sampling."""

from __future__ import annotations

import json
import socket
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "quant-service"))
sys.path.insert(0, str(Path("/Users/papa/codebase/.codex-wt/n8n-tdx-legacy-misc/quant-service")))
from app.datasources.sources import tdx_legacy_misc as legacy  # noqa: E402
from app.datasources.sources import tdx_protocol  # noqa: E402

LEGACY_HOSTS = (("60.191.117.167", 7709), ("218.75.126.9", 7709), ("117.34.114.13", 7709))
MAC_HOSTS = (("121.36.248.138", 7709), ("123.60.47.136", 7709), ("121.37.207.165", 7709))
INDEXES = ((1, "999999"), (0, "399001"), (0, "399006"), (0, "399300"), (1, "000300"), (1, "000905"), (1, "880761"), (2, "899050"))


class ProbeClient(tdx_protocol.TdxClient):
    """Use only the legacy LOGIN_ONE setup packet for this probe."""

    def __enter__(self) -> "ProbeClient":
        self._socket = socket.create_connection((self.host, self.port), timeout=self.timeout)
        self._exchange(tdx_protocol._SETUP_COMMANDS[0])
        return self

    def call(self, request: bytes) -> bytes:
        return self._exchange(request)

    def index_info(self, market: int, code: str) -> dict:
        return legacy.parse_index_info(self.call(legacy.build_index_info_request(market, code)))


def legacy_probe(host: tuple[str, int]) -> dict:
    result = {"host": f"{host[0]}:{host[1]}", "indexes": {}, "commands": {}}
    try:
        with ProbeClient(*host, 5) as client:
            for market, code in INDEXES:
                try:
                    row = client.index_info(market, code)
                    result["indexes"][f"{market}:{code}"] = {"fields": sorted(row), "up_count": row.get("up_count"), "down_count": row.get("down_count"), "order_count": row.get("order_count")}
                except (OSError, socket.timeout, legacy.tdx_protocol.TdxProtocolError, ValueError, IndexError, struct.error) as exc:
                    result["indexes"][f"{market}:{code}"] = {"error": f"{type(exc).__name__}: {exc}"}
            for code in ("000001", "600519"):
                try:
                    raw = client.call(legacy.build_simple_request(0x124A, struct.pack("<II5s", 0, 128000, bytes(5)), packet_type=1))
                    result["commands"][f"0x124a:{code}"] = {"raw_length": len(raw), "hex": raw.hex()}
                except (OSError, socket.timeout, legacy.tdx_protocol.TdxProtocolError) as exc:
                    result["commands"][f"0x124a:{code}"] = {"error": f"{type(exc).__name__}: {exc}"}
                break
            try:
                raw = client.call(legacy.build_chart_sampling_request(0, "000001"))
                result["commands"]["0x0fd1"] = {"raw_length": len(raw), "hex": raw.hex()[:160]}
            except (OSError, socket.timeout, legacy.tdx_protocol.TdxProtocolError) as exc:
                result["commands"]["0x0fd1"] = {"error": f"{type(exc).__name__}: {exc}"}
    except (OSError, socket.timeout, legacy.tdx_protocol.TdxProtocolError) as exc:
        result["setup_error"] = f"{type(exc).__name__}: {exc}"
    return result


def mac_probe(host: tuple[str, int]) -> dict:
    result = {"host": f"{host[0]}:{host[1]}", "commands": {}}
    try:
        with ProbeClient(*host, 5) as client:
            for name, opcode, payload in (("0x1215", 0x1215, struct.pack("<I70s30s", 0, b"", b"")), ("0x1217", 0x1217, struct.pack("<III70s30s", 1, 0, 30000, b"", b""))):
                try:
                    raw = client.call(legacy.build_simple_request(opcode, payload, packet_type=1))
                    result["commands"][name] = {"raw_length": len(raw), "hex": raw.hex()[:200]}
                except (OSError, socket.timeout, legacy.tdx_protocol.TdxProtocolError) as exc:
                    result["commands"][name] = {"error": f"{type(exc).__name__}: {exc}"}
    except (OSError, socket.timeout, legacy.tdx_protocol.TdxProtocolError) as exc:
        result["setup_error"] = f"{type(exc).__name__}: {exc}"
    return result


def main() -> int:
    reports = {"legacy": [legacy_probe(host) for host in LEGACY_HOSTS], "mac": [mac_probe(host) for host in MAC_HOSTS]}
    print(json.dumps(reports, ensure_ascii=False, sort_keys=True))
    decoded = any(value.get("up_count") is not None and value.get("down_count") is not None
                  for row in reports["legacy"] for value in row.get("indexes", {}).values())
    return 0 if decoded else 1


if __name__ == "__main__":
    raise SystemExit(main())
