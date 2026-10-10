#!/usr/bin/env python3
"""Probe up to six pooled extended-market hosts and map one usable ExHq service.

Read-only protocol calls only.  The script exits non-zero when no host returns
both a valid setup response and usable category/instrument rows.
"""

from __future__ import annotations

import json
import socket
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "quant-service"))
from app.datasources.sources.tdx_ex_market import TdxExMarketClient, TdxExMarketError  # noqa: E402

PORTS = {7727, 7720, 7719, 7615}
POOLS = (ROOT / "scripts/data/tdx_host_candidates.txt", ROOT / "scripts/data/tdx_host_candidates_other.txt")


def candidates() -> list[tuple[str, int]]:
    seen = set()
    out = []
    for path in POOLS:
        for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            value = raw.split("#", 1)[0].strip()
            if not value or ":" not in value:
                continue
            host, port = value.rsplit(":", 1)
            if int(port) not in PORTS or host in seen:
                continue
            seen.add(host)
            out.append((host, int(port)))
    return out[:6]


def _pick(rows, predicate):
    return next((r for r in rows if predicate(r)), None)


def _instrument_scan(client, total: int, *, max_pages: int = 64):
    """Bound list enumeration while still looking for the requested symbols."""
    rows = []
    for page in range(min(max_pages, (total + 99) // 100)):
        chunk = client.instruments(page * 100, 100)
        rows.extend(chunk)
        if len(chunk) < 100:
            break
    return rows


def main() -> int:
    attempts = []
    for host, port in candidates():
        print(json.dumps({"probe": f"{host}:{port}"}), flush=True)
        try:
            with TdxExMarketClient(host, port, 5.0) as client:
                login = client.login()
                count = client.count()
                categories = client.categories()
                instruments = _instrument_scan(client, count)
                if not categories or not instruments:
                    raise TdxExMarketError("empty categories or instruments")
                samples = {}
                for label, market, code in (
                    ("hk_00700", 31, "00700"),
                    ("us_AAPL", 74, "AAPL"),
                ):
                    try:
                        samples[label] = {
                            "quote": client.quote(market, code),
                            "bars": client.klines(9, market, code, 0, 5),
                        }
                    except Exception as error:  # informational per symbol
                        samples[label] = {"error": type(error).__name__}
                future = _pick(
                    instruments,
                    lambda r: (
                        r["market_id"] in (47, 60)
                        and r["code"].upper().startswith("IF")
                    ),
                )
                if future:
                    samples["if"] = {
                        "instrument": future,
                        "quote": client.quote(future["market_id"], future["code"]),
                        "bars": client.klines(
                            4, future["market_id"], future["code"], 0, 5
                        ),
                    }
                index = _pick(
                    instruments,
                    lambda r: (
                        r["market_id"] in (12, 27, 75) and r["category"] in (5, 12)
                    ),
                )
                if index:
                    samples["international_index"] = {
                        "instrument": index,
                        "quote": client.quote(index["market_id"], index["code"]),
                    }
                fx = _pick(
                    instruments,
                    lambda r: (
                        "CNH" in r["code"].upper() or "USD/CNH" in r["name"].upper()
                    ),
                )
                if fx:
                    samples["usd_cnh"] = {
                        "instrument": fx,
                        "quote": client.quote(fx["market_id"], fx["code"]),
                    }
                print(
                    json.dumps(
                        {
                            "host": f"{host}:{port}",
                            "login": login,
                            "count": count,
                            "categories": categories,
                            "instrument_rows": len(instruments),
                            "samples": samples,
                        },
                        ensure_ascii=False,
                        default=str,
                    )
                )
                return 0
        except (
            OSError,
            socket.timeout,
            TdxExMarketError,
            ValueError,
            IndexError,
            struct.error,
        ) as error:  # type: ignore[name-defined]
            attempts.append({"host": f"{host}:{port}", "error": type(error).__name__})
            print(
                json.dumps({"host": f"{host}:{port}", "error": type(error).__name__}),
                flush=True,
            )
    print(json.dumps({"usable": False, "attempts": attempts}, ensure_ascii=False))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
