#!/usr/bin/env python3
"""Inventory and verify every member in a downloaded TDX ``zhb.zip``."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
import zipfile

EXPECTED_MEMBERS = frozenset('''addedcode_bj.cfg brkcomp.dat brkseat.dat csiblock.dat hkblock.dat hkzsinfo.cfg hqrule.dat hspy.dat ihelp.dat ilong.dat importzs.cfg incon.dat jjblock.dat mgblock.dat nacomte.dat nbcomte.dat needini.dat neednote.dat nscomte.dat nscomte_std.dat nvcomte.dat nzcomte.dat othersg.cfg profile.dat pttab.dat relation.dat sbblock.dat sgxblock.dat spblock.dat tdxadr.cfg tdxahrate.cfg tdxbjmore.cfg tdxbk.cfg tdxchain.cfg tdxdszs.cfg tdxhkag.cfg tdxmgag.cfg tdxpkmore.cfg tdxsbzs.cfg tdxstat.cfg tdxstat2.cfg tdxzs.cfg tdxzs3.cfg tend_std.cfg tipinfo.dat ukblock.dat xgsg.cfg'''.split())


def _encoding(data: bytes) -> str:
    try:
        data.decode("utf-8-sig")
        return "utf-8"
    except UnicodeDecodeError:
        try:
            data.decode("gb18030")
            return "gb18030"
        except UnicodeDecodeError:
            return "binary"


def inspect(path: Path) -> tuple[list[str], int]:
    errors: list[str] = []
    with zipfile.ZipFile(path) as archive:
        names = {info.filename.rsplit("/", 1)[-1] for info in archive.infolist() if not info.is_dir()}
        missing = EXPECTED_MEMBERS - names
        extra = names - EXPECTED_MEMBERS
        errors.extend(f"missing member: {name}" for name in sorted(missing))
        errors.extend(f"unexpected member: {name}" for name in sorted(extra))
        for name in sorted(names):
            data = archive.read(next(info for info in archive.infolist() if info.filename.rsplit("/", 1)[-1] == name))
            if not data:
                errors.append(f"empty member: {name}")
            encoding = _encoding(data)
            if encoding != "binary":
                lines = data.decode(encoding, "replace").splitlines()
                columns = sorted({len(line.split("|")) for line in lines if line and not line.startswith("#")})
                sample = next((line[:120] for line in lines if line and not line.startswith("#")), "")
                print(f"{name}\t{encoding}\tlines={len(lines)}\tcolumns={columns}\tsample={sample}")
            else:
                print(f"{name}\tbinary\tbytes={len(data)}\tprefix16={data[:16].hex()}")
    return sorted(errors), len(names)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("zip", type=Path)
    args = parser.parse_args()
    if not args.zip.exists() or args.zip.stat().st_size == 0:
        print(f"required ZIP missing/empty: {args.zip}", file=sys.stderr)
        return 2
    try:
        errors, count = inspect(args.zip)
    except (OSError, zipfile.BadZipFile) as error:
        print(f"cannot inspect ZIP: {error}", file=sys.stderr)
        return 2
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    print(f"verified {count} zhb.zip members")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
