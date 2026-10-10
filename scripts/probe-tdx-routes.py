#!/usr/bin/env python3
"""Probe candidate TDX routes with the repository's stdlib client.

Read-only market-data requests.  Each host gets one connection and each
operation is represented by a row count or exception class in the JSON output.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date
from pathlib import Path

for candidate in (Path.cwd(), Path(__file__).resolve().parents[1] / "quant-service"):
    if (candidate / "app").is_dir():
        sys.path.insert(0, str(candidate))
        break

from app.datasources.sources.tdx_protocol import TdxClient, market_code  # noqa: E402


def read_hosts(path: Path) -> list[tuple[str, int]]:
    result = []
    seen = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        value = line.split("#", 1)[0].strip()
        if not value or ":" not in value:
            continue
        host, port = value.rsplit(":", 1)
        item = (host, int(port))
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


def operation(client: TdxClient, name: str):
    sz = market_code("000001.SZ")
    sh = market_code("600519.SH")
    if name == "quotes":
        return client.quotes([sz, sh])
    if name == "daily_bars":
        return client.bars(9, *sz, 0, 5)
    if name == "1m_bars":
        return client.bars(8, *sz, 0, 5)
    if name == "today_ticks":
        return client.ticks(*sz, date.today(), max_requests=2)
    if name == "xdxr":
        return client.xdxr(*sz)
    raise ValueError(name)


def probe(host: str, port: int, timeout: float) -> dict:
    row = {"host": host, "port": port, "connect_ms": None, "commands": {}}
    started = time.monotonic()
    try:
        with TdxClient(host, port, timeout) as client:
            row["connect_ms"] = round((time.monotonic() - started) * 1000, 1)
            for name in ("quotes", "daily_bars", "1m_bars", "today_ticks", "xdxr"):
                try:
                    value = operation(client, name)
                    row["commands"][name] = {"rows": len(value)}
                except Exception as error:  # protocol differences are matrix data
                    row["commands"][name] = {"error": type(error).__name__}
    except Exception as error:
        row["connect_ms"] = round((time.monotonic() - started) * 1000, 1)
        row["connect_error"] = type(error).__name__
    return row


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hosts", type=Path, default=Path(__file__).parent / "data/tdx_host_candidates.txt")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--timeout", type=float, default=5.0)
    args = parser.parse_args()
    results = [probe(host, port, args.timeout) for host, port in read_hosts(args.hosts)]
    payload = {"date": date.today().isoformat(), "results": results}
    text = json.dumps(payload, ensure_ascii=True, indent=2) + "\n"
    if args.output:
        args.output.write_text(text, encoding="utf-8")
    print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
