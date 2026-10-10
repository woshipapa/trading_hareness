#!/usr/bin/env python3
"""Read-only TDX F10/finance probe (never sends trading commands)."""

from __future__ import annotations

import os
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "quant-service"))

from app.datasources.sources import tdx_f10_finance as f10  # noqa: E402
from app.datasources.sources import tdx_files, tdx_protocol  # noqa: E402


HOSTS = (
    ("117.34.114.13", 7709), ("117.34.114.14", 7709),
    ("117.34.114.15", 7709), ("117.34.114.18", 7709),
    ("120.76.152.87", 7709), ("119.147.212.81", 7709),
)
SYMBOLS = ((0, "000001"), (1, "600519"))
#: What a probe step may fail with: the connection, or a reply that does not parse.  Anything else is a bug.
PROBE_ERRORS = (OSError, tdx_protocol.TdxProtocolError)


def main() -> int:
    timeout = float(os.environ.get("TDX_F10_TIMEOUT", "5"))
    usable = 0
    print("TDX F10 probe: read-only; no trading commands; one socket per host")
    for host, port in HOSTS:
        print(f"HOST {host}:{port}")
        try:
            with tdx_protocol.TdxClient(host, port, timeout) as client:
                if host in {"120.76.152.87", "119.147.212.81"}:
                    try:
                        manifest = tdx_files.download(client, "tdxfin/gpcw.txt")
                        print(f"  gpcw.txt bytes={len(manifest)} preview={manifest[:100]!r}")
                    except PROBE_ERRORS as exc:
                        print(f"  gpcw.txt ERROR {type(exc).__name__}: {exc}")
                for market, code in SYMBOLS:
                    label = f"{code}.{'SZ' if market == 0 else 'SH'}"
                    try:
                        finance = f10.parse_finance_info(client._exchange(f10.build_finance_info_request(market, code)))
                        usable += 1
                        print(f"  {label} finance fields={len(finance)} code={finance.get('code')} "
                              f"total_shares={finance.get('total_shares')} net_profit={finance.get('net_profit')} "
                              f"eps={finance.get('eps')}")
                    except PROBE_ERRORS as exc:
                        print(f"  {label} finance ERROR {type(exc).__name__}: {exc}")
                    try:
                        categories = f10.parse_company_categories(
                            client._exchange(f10.build_company_categories_request(market, code)))
                        usable += bool(categories)
                        print(f"  {label} categories rows={len(categories)} "
                              f"first={categories[0] if categories else None}")
                        if categories:
                            item = categories[0]
                            content = f10.parse_company_content(client._exchange(f10.build_company_content_request(
                                market, code, item["filename"], item["start"], min(item["length"], 256))))
                            print(f"  {label} content chars={len(content)} preview={content[:80]!r}")
                    except PROBE_ERRORS as exc:
                        print(f"  {label} company ERROR {type(exc).__name__}: {exc}")
        except PROBE_ERRORS as exc:
            print(f"  CONNECT ERROR {type(exc).__name__}: {exc}")
    print("SECOND SOURCE Tencent quote field 73 total shares")
    for market, code in SYMBOLS:
        try:
            prefix = "sz" if market == 0 else "sh"
            url = f"https://qt.gtimg.cn/q={prefix}{code}"
            with urllib.request.urlopen(url, timeout=timeout) as response:
                line = response.read().decode("gbk", "replace")
            values = line.split('="', 1)[1].rstrip('";\r\n').split("~")
            print(f"  {code} name={values[1]} total_shares={values[73]} compared_field=TDX total_shares")
        except Exception as exc:
            print(f"  {code} ERROR {type(exc).__name__}: {exc}")
    if not usable:
        print("no usable TDX F10/finance rows", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
