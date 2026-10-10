#!/usr/bin/env python3
"""Bounded, step-isolated TDX instrument verification."""
from __future__ import annotations

import argparse
import json
import socket
import sys
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "quant-service"))
from app.datasources.sources import tdx_instruments as ti  # noqa: E402

REQUIRED = [(1, "999999"), (1, "880761"), (0, "399300"), (0, "510300"), (0, "127045"), (2, "920000")]


def synthetic() -> dict[str, object]:
    rows = [{"market": 1, "code": "600000", "name": "示例", "decimal_point": 2},
            {"market": 1, "code": "688001", "name": "ST示例", "decimal_point": 2},
            {"market": 0, "code": "300001", "name": "创业", "decimal_point": 2},
            {"market": 2, "code": "920000", "name": "北证", "decimal_point": 2}]
    return {"mode": "synthetic", "type_counts": dict(ti.type_counts(rows)), "rows": len(rows)}


def _step(steps: dict[str, str], name: str, fn: Callable[[], Any], *, required: bool = True) -> Any:
    try:
        value = fn()
        if value is None or value == [] or value == {}:
            raise RuntimeError("no usable rows")
        steps[name] = "ok"
        return value
    except Exception as exc:  # one dead command must not hide later sections
        steps[name] = f"error:{type(exc).__name__}"
        return None


def _with_client(host: str, port: int, fn: Callable[[ti.TdxInstrumentClient], Any]) -> Any:
    with ti.TdxInstrumentClient(host, port) as client:
        return fn(client)


def live(host: str, port: int, zhb_path: str | None) -> tuple[dict[str, object], bool]:
    result: dict[str, object] = {"mode": "live", "host": f"{host}:{port}", "steps": {}}
    steps = result["steps"]  # type: ignore[assignment]
    assert isinstance(steps, dict)
    required_failed = False

    counts: dict[str, int] = {}
    for market, label in ((0, "SZ"), (1, "SH"), (2, "BJ")):
        value = _step(steps, f"security_count_{label}",
                      lambda market=market: _with_client(host, port, lambda c: c.security_count(market)))
        if value is None:
            required_failed = True
        else:
            counts[label] = int(value)
    result["counts"] = counts

    # BJ list variants are deliberately isolated: a timeout closes the socket,
    # but must not poison count, SZ/SH, or quote/bar sections.
    bj_variants: dict[str, str] = {}
    for new, page in ((False, 1000), (False, 100), (True, 1000), (True, 100)):
        label = f"bj_list_{'044d' if new else '0450'}_page{page}"
        def variant(new=new, page=page) -> list[dict[str, Any]]:
            return _with_client(host, port, lambda c: c.security_list(2, use_new=new, page_size=page))
        value = _step(steps, label, variant, required=False)
        bj_variants[label] = f"rows:{len(value)}" if value is not None else steps[label]
    result["bj_list_variants"] = bj_variants

    lists: dict[str, list[dict[str, Any]]] = {}
    for market, label in ((0, "SZ"), (1, "SH")):
        value = _step(steps, f"security_list_{label}",
                      lambda market=market: _with_client(host, port, lambda c: c.security_list(market)))
        if value is None:
            required_failed = True
        else:
            lists[label] = [dict(row, source="server_list") for row in value]
    if zhb_path:
        def read_zhb() -> list[dict[str, Any]]:
            import zipfile
            with zipfile.ZipFile(zhb_path) as archive:
                files = {Path(name).name: archive.read(name) for name in archive.namelist()}
            return ti.bj_rows_from_zhb(files)
        bj_rows = _step(steps, "bj_zhb_tdxbjmore", read_zhb)
        if bj_rows is not None:
            lists["BJ"] = bj_rows
            result["bj_zhb_rows"] = len(bj_rows)
            result["bj_count_difference"] = (len(bj_rows) - counts.get("BJ", 0)) if "BJ" in counts else None
    result["list_counts"] = {label: len(rows) for label, rows in lists.items()}
    result["type_counts"] = dict(ti.type_counts(row for rows in lists.values() for row in rows))

    required_results: dict[str, dict[str, Any]] = {}
    for market, code in REQUIRED:
        def probe(market=market, code=code) -> dict[str, Any]:
            def do(c: ti.TdxInstrumentClient) -> dict[str, Any]:
                quote = c.quotes([(market, code)])
                bars = c.index_bars(market, code, count=5) if code in {"999999", "880761", "399300", "510300"} else []
                if not quote:
                    raise RuntimeError("quote returned no rows")
                if code in {"999999", "880761", "399300", "510300"} and not bars:
                    raise RuntimeError("bars returned no rows")
                return {"quote": quote[0], "bars": bars}
            return _with_client(host, port, do)
        value = _step(steps, f"required_{code}", probe)
        if value is None:
            required_failed = True
        else:
            required_results[code] = value
    result["required_results"] = required_results
    return result, required_failed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--synthetic", action="store_true")
    parser.add_argument("--host", default="60.191.117.167")
    parser.add_argument("--port", type=int, default=7709)
    parser.add_argument("--zhb", help="local zhb.zip downloaded through the sibling file client")
    args = parser.parse_args()
    if args.synthetic:
        print(json.dumps({"status": "ok", **synthetic()}, ensure_ascii=False, sort_keys=True))
        return 0
    try:
        payload, required_failed = live(args.host, args.port, args.zhb)
    except (OSError, socket.timeout) as exc:
        payload, required_failed = {"mode": "live", "host": f"{args.host}:{args.port}",
                                    "steps": {"startup": f"error:{type(exc).__name__}"}}, True
    print(json.dumps({"status": "failed" if required_failed else "ok", **payload}, ensure_ascii=False, sort_keys=True))
    return 1 if required_failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
