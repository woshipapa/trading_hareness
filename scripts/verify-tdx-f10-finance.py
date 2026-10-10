#!/usr/bin/env python3
"""Read-only TDX F10/finance probe (never sends trading commands)."""

from __future__ import annotations

import os
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "quant-service"))

from app.datasources.sources.tdx_f10_finance import TdxF10Client  # noqa: E402


HOSTS = (
    ("117.34.114.13", 7709), ("117.34.114.14", 7709),
    ("117.34.114.15", 7709), ("117.34.114.18", 7709),
    ("120.76.152.87", 7709), ("119.147.212.81", 7709),
)
SYMBOLS = ((0, "000001"), (1, "600519"))


def main() -> int:
    timeout = float(os.environ.get("TDX_F10_TIMEOUT", "5"))
    usable = 0
    print("TDX F10 probe: read-only; no trading commands; one socket per host")
    for host, port in HOSTS:
        print(f"HOST {host}:{port}")
        try:
            with TdxF10Client(host, port, timeout_seconds=timeout) as client:
                if host in {"120.76.152.87", "119.147.212.81"}:
                    try:
                        manifest = client.report_file("tdxfin/gpcw.txt", max_bytes=65536)
                        print(f"  gpcw.txt bytes={len(manifest)} preview={manifest[:100]!r}")
                    except Exception as exc:
                        print(f"  gpcw.txt ERROR {type(exc).__name__}: {exc}")
                for market, code in SYMBOLS:
                    label = f"{code}.{'SZ' if market == 0 else 'SH'}"
                    try:
                        finance = client.finance_info(market, code)
                        usable += 1
                        print(f"  {label} finance fields={len(finance)} code={finance.get('code')} "
                              f"total_shares={finance.get('total_shares')} net_profit={finance.get('net_profit')} "
                              f"eps={finance.get('eps')}")
                    except Exception as exc:
                        print(f"  {label} finance ERROR {type(exc).__name__}: {exc}")
                    try:
                        categories = client.company_categories(market, code)
                        usable += bool(categories)
                        print(f"  {label} categories rows={len(categories)} "
                              f"first={categories[0] if categories else None}")
                        if categories:
                            item = categories[0]
                            content = client.company_content(market, code, item["filename"], item["start"], min(item["length"], 256))
                            print(f"  {label} content chars={len(content)} preview={content[:80]!r}")
                    except Exception as exc:
                        print(f"  {label} company ERROR {type(exc).__name__}: {exc}")
        except Exception as exc:
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
