#!/usr/bin/env python3
"""Export read-only TdxQuant market data to the offline CSV contracts.

Owner-workstation only: the TDX terminal/tqcenter installation is required.
This script never imports trading APIs and must not be run on the research Mac.
"""

from __future__ import annotations

import argparse
import csv
import importlib
import sys
from pathlib import Path
from typing import Any

for candidate in (Path.cwd(), Path(__file__).resolve().parents[1] / "quant-service"):
    if (candidate / "app").is_dir():
        sys.path.insert(0, str(candidate))
        break

DAILY_CSV_COLUMNS = ("ts_code", "trade_date", "open", "high", "low", "close", "volume_shares", "amount_yuan")
MINUTE_CSV_COLUMNS = ("ts_code", "datetime", "open", "high", "low", "close", "volume", "amount", "source_available_at")


class TdxQuantUnavailableError(RuntimeError):
    """The owner workstation does not provide the official TdxQuant module."""


def _load_tq() -> Any:
    try:
        module = importlib.import_module("tqcenter")
        return module.tq
    except (ImportError, AttributeError) as exc:
        raise TdxQuantUnavailableError(
            "tqcenter is unavailable; run this script only on the owner Windows TDX workstation"
        ) from exc


def write_csv(path: Path, columns: tuple[str, ...], rows: list[dict[str, Any]]) -> int:
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(columns), extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)


def _series_items(value: Any):
    if hasattr(value, "items"):
        return value.items()
    return enumerate(value)


def _rows(result: Any, symbols: list[str], kind: str) -> list[dict[str, Any]]:
    if not isinstance(result, dict):
        raise ValueError("TdxQuant get_market_data returned a non-dict result")
    fields = {str(key).lower(): value for key, value in result.items()}
    required = ("time", "open", "high", "low", "close", "volume", "amount")
    if any(field not in fields for field in required):
        raise ValueError("TdxQuant result is missing one or more OHLCV fields")
    by_symbol: dict[str, dict[str, Any]] = {}
    for field in required:
        for symbol, series in _series_items(fields[field]):
            by_symbol.setdefault(str(symbol), {})[field] = series
    rows: list[dict[str, Any]] = []
    wanted = {symbol.upper() for symbol in symbols}
    for symbol, values in by_symbol.items():
        if wanted and symbol.upper() not in wanted:
            continue
        series = values["time"]
        points = _series_items(series)
        lookup = {field: dict(_series_items(values[field]))
                  for field in ("open", "high", "low", "close", "volume", "amount")}
        for key, stamp in points:
            stamp_text = str(stamp)[:19].replace("T", " ")
            if kind == "daily":
                date = stamp_text[:10].replace("/", "-")
                rows.append({"ts_code": symbol, "trade_date": date, "open": float(lookup["open"][key]),
                             "high": float(lookup["high"][key]), "low": float(lookup["low"][key]),
                             "close": float(lookup["close"][key]),
                             "volume_shares": float(lookup["volume"][key]) * 100,
                             "amount_yuan": float(lookup["amount"][key]) * 10000})
            else:
                rows.append({"ts_code": symbol, "datetime": stamp_text, "open": float(lookup["open"][key]),
                             "high": float(lookup["high"][key]), "low": float(lookup["low"][key]),
                             "close": float(lookup["close"][key]),
                             "volume": float(lookup["volume"][key]) * 100,
                             "amount": float(lookup["amount"][key]) * 10000})
    return rows


def export(tq: Any, args: argparse.Namespace) -> list[Path]:
    tq.initialize(__file__)
    symbols = [symbol.strip().upper() for symbol in args.symbols.split(",") if symbol.strip()]
    outputs: list[Path] = []
    for kind in (part.strip() for part in args.kinds.split(",") if part.strip()):
        period = {"daily": "1d", "1m": "1m", "5m": "5m"}.get(kind)
        if period is None:
            raise ValueError(f"unsupported kind: {kind}")
        result = tq.get_market_data(field_list=[], stock_list=symbols, start_time=args.start,
                                    end_time=args.end, count=-1, dividend_type="none",
                                    period=period, fill_data=False)
        rows = _rows(result, symbols, kind)
        if not rows:
            print(f"{kind}: no rows")
            continue
        first = min(str(row.get("datetime") or row.get("trade_date"))[:10] for row in rows).replace("-", "")
        last = max(str(row.get("datetime") or row.get("trade_date"))[:10] for row in rows).replace("-", "")
        target = args.out / f"tdx_quant_{kind}_{first}_{last}.csv"
        write_csv(target, DAILY_CSV_COLUMNS if kind == "daily" else MINUTE_CSV_COLUMNS, rows)
        outputs.append(target)
        print(f"{kind}: {len(rows)} rows -> {target}")
    return outputs


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--symbols", required=True, help="comma-separated TDX symbols, e.g. 600000.SH")
    parser.add_argument("--kinds", default="1m", help="comma list of daily,1m,5m")
    parser.add_argument("--start", default="", help="YYYYMMDD")
    parser.add_argument("--end", default="", help="YYYYMMDD")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)
    print(f"plan: tqcenter.get_market_data -> {args.out} kinds={args.kinds} symbols={args.symbols}")
    if args.dry_run:
        return 0
    try:
        tq = _load_tq()
    except TdxQuantUnavailableError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    export(tq, args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
