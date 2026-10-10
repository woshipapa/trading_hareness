#!/usr/bin/env python3
"""Probe TDX board/report files once per host and print JSON evidence.

This is deliberately a small read-only probe: one connection per host, one
download attempt per filename, a 5-second socket timeout, and the client's
64 MiB cap.  ``zhb.zip`` is unpacked locally so archive members are reported
without issuing separate requests for the same data.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

for candidate in (Path.cwd(), Path(__file__).resolve().parents[1] / "quant-service"):
    if (candidate / "app").is_dir():
        sys.path.insert(0, str(candidate))
        break

from app.datasources.sources import tdx_files  # noqa: E402


HOSTS = (
    ("117.34.114.13", 7709),
    ("60.191.117.167", 7709),
    ("218.75.126.9", 7709),
    ("121.36.248.138", 7709),
)
BLOCK_NAMES = ("block_gn.dat", "block_fg.dat", "block_zs.dat")


def _summary(name: str, data: bytes) -> dict[str, object]:
    result: dict[str, object] = {"file": name, "size": len(data)}

    def compact(rows: list[dict[str, object]]) -> list[dict[str, object]]:
        compacted = []
        for row in rows[:2]:
            item = dict(row)
            if isinstance(item.get("members"), list):
                item["members"] = item["members"][:5]
                item["members_truncated"] = True
            compacted.append(item)
        return compacted

    try:
        if name in BLOCK_NAMES:
            rows = tdx_files.parse_block_file(data)
            result.update(member_counts=sum(row["member_count"] for row in rows), rows=len(rows), first_rows=compact(rows))
        elif name == "zhb.zip":
            members = tdx_files.parse_zhb_zip(data)
            result.update(member_count=len(members), members=sorted(members), first_rows={})
            for member_name in ("tdxzs.cfg", "tdxzs3.cfg", "spblock.dat", "tdxstat.cfg", "tdxstat2.cfg"):
                member = members.get(member_name)
                if member is None:
                    continue
                if member_name.startswith("tdxzs"):
                    rows = tdx_files.parse_tdxzs(member)
                elif member_name == "spblock.dat":
                    rows = tdx_files.parse_spblock(member)
                elif member_name == "tdxstat.cfg":
                    rows = tdx_files.parse_tdxstat(member)
                else:
                    rows = tdx_files.parse_tdxstat2(member)
                result.setdefault("first_rows", {})[member_name] = compact(rows)
                result[member_name + "_rows"] = len(rows)
        else:
            result["rows"] = 0
    except Exception as error:  # noqa: BLE001 - probe reports malformed upstream data
        result["parse_error"] = type(error).__name__ + ": " + str(error)
    return result


def main() -> int:
    for host, port in HOSTS:
        report: dict[str, object] = {"host": f"{host}:{port}", "files": []}
        try:
            with tdx_files.TdxFilesClient(host, port, timeout_seconds=5.0) as client:
                for filename in (*BLOCK_NAMES, "zhb.zip"):
                    try:
                        data = client.download(filename)
                        report["files"].append(_summary(filename, data))
                    except Exception as error:  # noqa: BLE001 - per-file availability is evidence
                        report["files"].append({"file": filename, "error": type(error).__name__ + ": " + str(error)})
        except Exception as error:  # noqa: BLE001 - host refusal is expected for public peers
            report["connection_error"] = type(error).__name__ + ": " + str(error)
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
