#!/usr/bin/env python3
"""Verify a bounded local GPCW history cache and fail on unusable rows."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "quant-service"))
from app.datasources.sources.tdx_fin_history import (  # noqa: E402
    TdxFinanceError, parse_gpcw_zip, parse_manifest, verify_manifest_entry,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    parser.add_argument("cache", type=Path)
    args = parser.parse_args()
    try:
        entries = parse_manifest(args.manifest.read_bytes())
    except (OSError, TdxFinanceError) as error:
        print(f"manifest unusable: {error}", file=sys.stderr)
        return 2
    usable = 0
    for entry in entries:
        path = args.cache / entry.filename
        if not path.exists() or path.stat().st_size == 0:
            continue
        try:
            payload = path.read_bytes()
            if not verify_manifest_entry(entry, payload):
                print(f"{entry.filename}: manifest MD5/size mismatch", file=sys.stderr)
                continue
            rows = parse_gpcw_zip(payload, filename=entry.filename)
        except (OSError, TdxFinanceError) as error:
            print(f"{entry.filename}: unusable: {error}", file=sys.stderr)
            continue
        if rows:
            usable += 1
            print(f"{entry.filename}: rows={len(rows)} fields={rows[0]['field_count']}")
    if not usable:
        print("no usable GPCW rows", file=sys.stderr)
        return 1
    print(f"verified {usable} periods")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
