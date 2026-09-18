"""Readers for the TDX client's downloaded files (盘后数据下载).

* ``vipdoc/{sh,sz,bj}/lday/<ex><code>.day`` -- daily bars, 32-byte records
  ``<IIIIIfII``: yyyymmdd, open/high/low/close in price units x100 (x1000
  for funds/bonds), amount (float yuan), volume (shares), reserved.
* ``vipdoc/{sh,sz,bj}/minline/*.lc1`` and ``fzline/*.lc5`` -- 1/5-minute
  bars, 32-byte records ``<HHfffffII``: packed date, minutes since midnight,
  open/high/low/close/amount as float, volume (shares), reserved.

Output rows use explicit units: minute rows match the offline minute CSV
contract (``ts_code, datetime, open, high, low, close, volume, amount``, volume
in shares like every other minute source here); daily rows carry
``volume_shares``/``amount_yuan`` and are research input, not canonical bars --
promotion to ``canonical_bars_daily`` stays with the provider arbitration.
"""

from __future__ import annotations

import csv
import struct
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Iterator

from ..http import ashare_symbol


RECORD_SIZE = 32
_DAY = struct.Struct("<IIIIIfII")
_MINUTE = struct.Struct("<HHfffffII")
MINUTE_CSV_COLUMNS = ("ts_code", "datetime", "open", "high", "low", "close", "volume", "amount", "source_available_at")
DAILY_CSV_COLUMNS = ("ts_code", "trade_date", "open", "high", "low", "close", "volume_shares", "amount_yuan")


def symbol_from_filename(path: Path) -> str | None:
    """``sh600000.day`` -> ``600000.SH`` for A-share equities only."""
    stem = path.stem.lower()
    if len(stem) != 8 or stem[:2] not in {"sh", "sz", "bj"}:
        return None
    return ashare_symbol(stem[2:], stem[:2].upper())


def price_scale(symbol: str) -> float:
    """Equities store prices x100; this reader only admits equities."""
    return 0.01


def _records(data: bytes) -> Iterator[bytes]:
    usable = len(data) - len(data) % RECORD_SIZE
    for offset in range(0, usable, RECORD_SIZE):
        yield data[offset:offset + RECORD_SIZE]


def parse_day_bytes(data: bytes, symbol: str) -> list[dict[str, Any]]:
    scale = price_scale(symbol)
    rows = []
    for record in _records(data):
        stamp, open_raw, high_raw, low_raw, close_raw, amount, volume, _ = _DAY.unpack(record)
        if stamp < 19900101 or close_raw <= 0:
            continue
        day = f"{stamp // 10000:04d}-{stamp % 10000 // 100:02d}-{stamp % 100:02d}"
        rows.append({
            "ts_code": symbol, "trade_date": day,
            "open": round(open_raw * scale, 4), "high": round(high_raw * scale, 4),
            "low": round(low_raw * scale, 4), "close": round(close_raw * scale, 4),
            "volume_shares": volume, "amount_yuan": round(float(amount), 2),
        })
    return rows


def parse_minute_bytes(data: bytes, symbol: str) -> list[dict[str, Any]]:
    rows = []
    for record in _records(data):
        packed_day, minutes, open_price, high, low, close, amount, volume, _ = _MINUTE.unpack(record)
        year = packed_day // 2048 + 2004
        month, day = (packed_day % 2048) // 100, (packed_day % 2048) % 100
        if not (1 <= month <= 12 and 1 <= day <= 31) or close <= 0:
            continue
        rows.append({
            "ts_code": symbol,
            "datetime": f"{year:04d}-{month:02d}-{day:02d} {minutes // 60:02d}:{minutes % 60:02d}:00",
            "open": round(open_price, 4), "high": round(high, 4), "low": round(low, 4), "close": round(close, 4),
            "volume": volume, "amount": round(float(amount), 2),
        })
    return rows


def read_file(path: Path) -> tuple[str, list[dict[str, Any]]]:
    """Return ``(kind, rows)`` where kind is ``daily``, ``1m`` or ``5m``."""
    symbol = symbol_from_filename(path)
    if symbol is None:
        raise ValueError(f"not an A-share TDX file name: {path.name}")
    data = path.read_bytes()
    suffix = path.suffix.lower()
    if suffix == ".day":
        return "daily", parse_day_bytes(data, symbol)
    if suffix in {".lc1", ".lc5", ".1", ".5"}:
        return ("1m" if suffix in {".lc1", ".1"} else "5m"), parse_minute_bytes(data, symbol)
    raise ValueError(f"unsupported TDX file type: {path.suffix}")


def discover(vipdoc: Path, kinds: Iterable[str] = ("daily", "1m")) -> Iterator[Path]:
    folders = {"daily": ("lday", "*.day"), "1m": ("minline", "*.lc1"), "5m": ("fzline", "*.lc5")}
    for exchange in ("sh", "sz", "bj"):
        for kind in kinds:
            folder, pattern = folders[kind]
            yield from sorted((vipdoc / exchange / folder).glob(pattern))


def write_csv(path: Path, columns: tuple[str, ...], rows: Iterable[dict[str, Any]]) -> int:
    count = 0
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(columns), extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
            count += 1
    return count


def file_available_at(path: Path) -> str:
    """The file's mtime: when the bars were demonstrably on disk."""
    return datetime.fromtimestamp(path.stat().st_mtime).astimezone().isoformat()


__all__ = [
    "DAILY_CSV_COLUMNS", "MINUTE_CSV_COLUMNS", "discover", "file_available_at", "parse_day_bytes",
    "parse_minute_bytes", "read_file", "symbol_from_filename", "write_csv",
]
