#!/usr/bin/env python3
"""Export TDX client files (vipdoc) to the platform's offline CSV contracts.

Run on the machine that has the TDX client's ``vipdoc`` directory (the owner
workstation), from ``quant-service`` so ``app`` is importable::

    python ../scripts/tdx-local-export.py --vipdoc "C:/new_tdx/vipdoc" \
        --out G:/StockPlatform/data/offline --kinds 1m --since 2026-09-01

Minute files become one ``tdx_<kind>_<date-range>.csv`` in the offline minute
contract, ready for ``POST /api/v1/market/minute/import-offline``; ``source_available_at``
is each file's mtime, never the bar time.  Daily files become a research CSV
with explicit ``volume_shares``/``amount_yuan`` units.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

for candidate in (Path.cwd(), Path(__file__).resolve().parents[1] / "quant-service"):
    if (candidate / "app").is_dir():
        sys.path.insert(0, str(candidate))
        break

from app.datasources.sources.tdx_local_files import (  # noqa: E402
    DAILY_CSV_COLUMNS, MINUTE_CSV_COLUMNS, discover, file_available_at, read_file, write_csv,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--vipdoc", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--kinds", default="1m", help="comma list of daily,1m,5m")
    parser.add_argument("--since", default="", help="keep rows on/after this date (YYYY-MM-DD)")
    parser.add_argument("--symbols", default="", help="optional comma list like 600000.SH,000001.SZ")
    args = parser.parse_args()
    kinds = tuple(kind.strip() for kind in args.kinds.split(",") if kind.strip())
    wanted = {symbol.strip().upper() for symbol in args.symbols.split(",") if symbol.strip()}
    args.out.mkdir(parents=True, exist_ok=True)
    for kind in kinds:
        rows: list[dict] = []
        files = 0
        for path in discover(args.vipdoc, (kind,)):
            try:
                file_kind, parsed = read_file(path)
            except ValueError:
                continue
            if wanted and parsed and parsed[0]["ts_code"] not in wanted:
                continue
            files += 1
            available = file_available_at(path)
            for row in parsed:
                stamp = row.get("datetime") or row.get("trade_date") or ""
                if args.since and stamp[:10] < args.since:
                    continue
                if file_kind != "daily":
                    row["source_available_at"] = available
                rows.append(row)
        if not rows:
            print(f"{kind}: no rows from {files} files")
            continue
        first = min(str(row.get("datetime") or row.get("trade_date"))[:10] for row in rows).replace("-", "")
        last = max(str(row.get("datetime") or row.get("trade_date"))[:10] for row in rows).replace("-", "")
        target = args.out / f"tdx_{kind}_{first}_{last}.csv"
        columns = DAILY_CSV_COLUMNS if kind == "daily" else MINUTE_CSV_COLUMNS
        count = write_csv(target, columns, rows)
        print(f"{kind}: {count} rows from {files} files -> {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
