#!/usr/bin/env python3
"""Recompute the headline hit-rates of the tdxstat.cfg and tdxstat2.cfg column dictionary.

Samples 420 symbols from the two statistics files, fetches daily bars and MAC
dynamic fields for them, and compares each mapped column with its reference.
Exits 1 when a column documented as CONFIRMED falls below 95 %.

Usage: probe-tdx-q-stats.py ZHB_DIR TDX_PKG_ROOT
  ZHB_DIR       directory holding tdxstat.cfg and tdxstat2.cfg
  TDX_PKG_ROOT  directory that contains the tdxpkg package (tdx_mac.py)
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
import random
import struct
import sys

MAC_HOST = ("121.36.248.138", 7709)
MAC_BITS = list(range(126))
SAMPLE_QUOTA = {"sz_main": 130, "sh_main": 110, "chinext": 60, "star": 60, "bj": 30, "etf": 30}
CONFIRM_LINE = 0.95
PLAUSIBLE_LINE = 0.60


@dataclass(frozen=True)
class Spec:
    file: str
    index: int
    name: str
    reference: str
    claimed: str
    scale: float = 1.0


SPECS = [
    Spec("tdxstat", 3, "PE static", "mac49", "CONFIRMED"),
    Spec("tdxstat", 4, "date", "last_date", "CONFIRMED"),
    Spec("tdxstat", 5, "up/down streak (signed)", "streak", "CONFIRMED"),
    Spec("tdxstat", 6, "change 1d %", "ret1", "CONFIRMED"),
    Spec("tdxstat", 7, "prev change %", "mac66", "CONFIRMED"),
    Spec("tdxstat", 8, "prev2 change %", "mac71", "CONFIRMED"),
    Spec("tdxstat", 9, "PE TTM", "mac48", "CONFIRMED"),
    Spec("tdxstat", 10, "dividend yield %", "mac91", "CONFIRMED"),
    Spec("tdxstat", 11, "circulating capital", "mac45", "CONFIRMED"),
    Spec("tdxstat", 17, "change ~20d %", "ret19", "PLAUSIBLE"),
    Spec("tdxstat", 18, "change 20d %", "mac59", "CONFIRMED"),
    Spec("tdxstat", 19, "change ~60d %", "ret59", "PLAUSIBLE"),
    Spec("tdxstat", 20, "change 60d %", "mac68", "CONFIRMED"),
    Spec("tdxstat", 21, "YTD %", "mac60", "CONFIRMED"),
    Spec("tdxstat", 23, "MAC bit 125 (unnamed)", "mac125", "CONFIRMED"),
    Spec("tdxstat", 26, "annual limit-up days", "mac88", "CONFIRMED"),
    Spec("tdxstat", 27, "change 4d %", "ret4", "CONFIRMED"),
    Spec("tdxstat", 28, "change 5d %", "mac69", "CONFIRMED"),
    Spec("tdxstat", 29, "change 9d %", "ret9", "PLAUSIBLE"),
    Spec("tdxstat", 30, "change 10d %", "mac70", "CONFIRMED"),
    Spec("tdxstat2", 2, "date", "last_date", "CONFIRMED"),
    Spec("tdxstat2", 3, "amount today (万)", "amt0", "CONFIRMED", 1e-4),
    Spec("tdxstat2", 5, "amount prev day (万)", "amt1", "CONFIRMED", 1e-4),
    Spec("tdxstat2", 7, "amount 2 days ago (万)", "amt2", "CONFIRMED", 1e-4),
    Spec("tdxstat2", 11, "MTD %", "mtd", "CONFIRMED"),
    Spec("tdxstat2", 12, "change 1y %", "mac65", "CONFIRMED"),
    Spec("tdxstat2", 14, "open auction amount (万)", "mac87", "CONFIRMED", 1e-4),
    Spec("tdxstat2", 17, "52-week high", "mac53", "CONFIRMED"),
    Spec("tdxstat2", 18, "52-week low", "mac54", "CONFIRMED"),
    Spec("tdxstat2", 19, "change ~30d %", "ret29", "PLAUSIBLE"),
    Spec("tdxstat2", 20, "change 30d %", "ret30", "PLAUSIBLE"),
]


def f32(value: int) -> float:
    return struct.unpack("<f", struct.pack("<I", value))[0]


def sample_symbols(zhb: Path) -> list[tuple[str, str]]:
    seen = set()
    for name in ("tdxstat.cfg", "tdxstat2.cfg"):
        for line in (zhb / name).read_text(encoding="utf-8").splitlines():
            market, code = line.split("|")[:2]
            seen.add((market, code))
    groups: dict[str, list[tuple[str, str]]] = {key: [] for key in SAMPLE_QUOTA}
    for market, code in sorted(seen):
        if market == "0" and code[:3] in ("000", "001", "002", "003"):
            groups["sz_main"].append((market, code))
        elif market == "0" and code[:3] in ("300", "301"):
            groups["chinext"].append((market, code))
        elif market == "1" and code[:3] in ("600", "601", "603", "605"):
            groups["sh_main"].append((market, code))
        elif market == "1" and code[:3] == "688":
            groups["star"].append((market, code))
        elif market == "2":
            groups["bj"].append((market, code))
        elif code[:2] in ("15", "16", "50", "51", "56", "58"):
            groups["etf"].append((market, code))
    rng = random.Random(7)
    picked: list[tuple[str, str]] = []
    for key, quota in SAMPLE_QUOTA.items():
        picked += rng.sample(groups[key], min(quota, len(groups[key])))
    return picked


def fetch(pick: list[tuple[str, str]], tdx_root: Path) -> tuple[dict, dict]:
    sys.path.insert(0, str(tdx_root))
    from tdxpkg import tdx_mac

    bitmap = bytearray(20)
    for bit in MAC_BITS:
        bitmap[bit // 8] |= 1 << (bit % 8)
    quotes: dict[str, dict[int, int]] = {}
    bars: dict[str, list[dict]] = {}
    stride = 68 + 4 * len(MAC_BITS)
    with tdx_mac.TdxMacClient(*MAC_HOST, 6) as client:
        for start in range(0, len(pick), 40):
            chunk = [(int(market), code) for market, code in pick[start:start + 40]]
            body = client._exchange(tdx_mac.build_batch_quotes_request(chunk, bytes(bitmap)))
            count = struct.unpack_from("<H", body, 24)[0]
            for k in range(count):
                pos = 26 + k * stride
                symbol = body[pos + 2:pos + 24].split(b"\0")[0].decode()
                values = struct.unpack_from("<" + "I" * len(MAC_BITS), body, pos + 68)
                quotes[symbol] = dict(zip(MAC_BITS, values))
        for market, code in pick:
            bars[code] = client.bars(int(market), code, 4, 0, 800)
    return quotes, bars


def ret(closes: list[float], days: int) -> float | None:
    return (closes[-1] / closes[-1 - days] - 1) * 100 if len(closes) > days else None


def streak(closes: list[float]) -> int:
    up = closes[-1] > closes[-2]
    count = 0
    for i in range(len(closes) - 1, 0, -1):
        if (closes[i] > closes[i - 1]) != up:
            break
        count += 1
    return count if up else -count


def mtd(bars: list[dict]) -> float:
    month = bars[-1]["date"][:7]
    first = next(i for i, bar in enumerate(bars) if bar["date"][:7] == month)
    return (bars[-1]["close"] / bars[first - 1]["close"] - 1) * 100


def references(quotes: dict[str, dict[int, int]], bars: dict[str, list[dict]], code: str) -> dict[str, float | str | None]:
    raw = quotes[code]
    series = bars[code]
    closes = [bar["close"] for bar in series]
    values: dict[str, float | str | None] = {
        "last_date": series[-1]["date"].replace("-", ""),
        "streak": streak(closes),
        "mtd": mtd(series),
        "amt0": series[-1]["amount"],
        "amt1": series[-2]["amount"],
        "amt2": series[-3]["amount"],
    }
    for days in (1, 4, 9, 19, 29, 30, 59):
        values[f"ret{days}"] = ret(closes, days)
    for bit in MAC_BITS:
        values[f"mac{bit}"] = raw[bit] if bit == 88 else f32(raw[bit])
    return values


def hit(actual: float, expected: float) -> bool:
    return abs(actual - expected) <= max(abs(expected) * 1e-3, 0.01)


def hit_rate(spec: Spec, rows: dict[str, list[str]], refs: dict[str, dict]) -> tuple[int, int]:
    hits = total = 0
    for code, cells in rows.items():
        text = cells[spec.index]
        expected = refs[code][spec.reference]
        if text == "" or expected is None:
            continue
        total += 1
        if spec.reference == "last_date":
            hits += text == expected
        else:
            hits += hit(float(text), float(expected) * spec.scale)
    return hits, total


def verdict(share: float) -> str:
    if share >= CONFIRM_LINE:
        return "CONFIRMED"
    return "PLAUSIBLE" if share >= PLAUSIBLE_LINE else "UNKNOWN"


def load_rows(zhb: Path, name: str, width: int, pick: set[str]) -> dict[str, list[str]]:
    rows = {}
    for line in (zhb / name).read_text(encoding="utf-8").splitlines():
        cells = line.split("|")
        if len(cells) == width and cells[1] in pick:
            rows[cells[1]] = cells
    return rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("zhb_dir", type=Path)
    parser.add_argument("tdx_pkg_root", type=Path)
    args = parser.parse_args()
    pick = sample_symbols(args.zhb_dir)
    quotes, bars = fetch(pick, args.tdx_pkg_root)
    refs = {code: references(quotes, bars, code) for _, code in pick}
    codes = set(refs)
    rows = {
        "tdxstat": load_rows(args.zhb_dir, "tdxstat.cfg", 35, codes),
        "tdxstat2": load_rows(args.zhb_dir, "tdxstat2.cfg", 21, codes),
    }
    print(f"sample symbols: {len(pick)}; MAC quotes: {len(quotes)}; bar series: {len(bars)}")
    failed = []
    for spec in SPECS:
        hits, total = hit_rate(spec, rows[spec.file], refs)
        share = hits / total if total else 0.0
        label = verdict(share)
        print(f"{spec.file}[{spec.index}] {spec.name}: ref={spec.reference} hits={hits}/{total} rate={share:.1%} verdict={label} claimed={spec.claimed}")
        if spec.claimed == "CONFIRMED" and share < CONFIRM_LINE:
            failed.append(f"{spec.file}[{spec.index}]")
    if failed:
        print("CONFIRMED columns below 95%: " + ", ".join(failed))
        return 1
    print("all CONFIRMED columns >= 95%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
