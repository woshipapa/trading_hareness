#!/usr/bin/env python3
"""Generate the old-to-new BJ stock code table of the TDX server from its addedcode_bj.cfg."""

import argparse
import hashlib
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "quant-service"))
from app.datasources.sources import tdx_files, tdx_protocol  # noqa: E402
from app.datasources.sources.tdx_zhb_extras import parse_bj_mapping  # noqa: E402

MEMBER = "addedcode_bj.cfg"
DEFAULT_OUTPUT = ROOT / "quant-service/app/datasources/sources/tdx_bj_codes.py"


def render(mapping, source, data):
    generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    lines = [
        '"""Generated old-to-new BJ stock codes of the TDX server; research evidence only."""',
        f"# source={source}", f"# md5={hashlib.md5(data).hexdigest()}", f"# generated_at_utc={generated_at}",
        "OLD_TO_NEW = {", *[f"    {old!r}: {new!r}," for old, new in sorted(mapping.items())], "}", "",
    ]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, help=f"a {MEMBER} file; without it zhb.zip is downloaded from a TDX host")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    if args.input:
        data, source = args.input.read_bytes(), args.input.name
    else:
        data, host = tdx_protocol.call_sync(
            lambda client: tdx_files.parse_zhb_zip(tdx_files.download(client, "zhb.zip"))[MEMBER])
        source = f"zhb.zip member {MEMBER} from {host}"
    mapping = parse_bj_mapping(data)
    if not mapping:
        print(f"{source} holds no old-to-new BJ code", file=sys.stderr)
        return 2
    args.output.write_text(render(mapping, source, data), encoding="utf-8")
    print(f"wrote {args.output} ({len(mapping)} codes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
