"""Read-only parsers for the currently unused members of ``zhb.zip``.

The TDX files are vendor configuration snapshots.  Parsers return the raw
columns alongside only the fields that are directly evidenced by the file
shape; no row is promoted to a live strategy or order path.
"""

from __future__ import annotations

import math
import re
from typing import Any

from .tdx_protocol import decode_text



def _lines(data: bytes) -> list[str]:
    return [line.strip("\x00\r") for line in decode_text(data).splitlines() if line.strip("\x00\r")]


def _number(value: str) -> float | None:
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def parse_holiday_calendar(needini: bytes, hqrule: bytes = b"") -> dict[str, Any]:
    """Parse ``needini.dat`` YN holiday dates and ``hqrule.dat`` key/value rules.

    ``holidays`` contains date strings in YYYY-MM-DD form.  The files list
    holidays, not open-day overrides; callers must still reconcile them with a
    persisted exchange calendar.
    """
    holidays: set[str] = set()
    declared = None
    for line in _lines(needini):
        if line.startswith("NUM="):
            try:
                declared = int(line[4:])
            except ValueError:
                pass
        match = re.match(r"Y\d+=(\d{4}),(.+)", line)
        if not match:
            continue
        year, values = match.groups()
        for value in values.split(","):
            if re.fullmatch(r"\d{4}", value):
                holidays.add(f"{year}-{value[:2]}-{value[2:]}")
    section = ""
    rules: dict[str, str] = {}
    for line in _lines(hqrule):
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1]
        elif "=" in line:
            key, value = line.split("=", 1)
            rules[f"{section}.{key}" if section else key] = value
    return {"holidays": sorted(holidays), "declared_year_count": declared, "rules": rules}


def _pipe_rows(data: bytes, minimum: int = 1) -> list[list[str]]:
    return [fields for line in _lines(data) if not line.startswith("#")
            for fields in [line.split("|")] if len(fields) >= minimum]


def parse_bj_mapping(addedcode: bytes, bjmore: bytes = b"") -> dict[str, Any]:
    """Parse legacy-to-current Beijing code migration and BJ metadata rows."""
    if not bjmore:
        mapping: dict[str, str] = {}
        for line in _lines(addedcode):
            codes = re.findall(r"(?<!\d)(\d{6})(?!\d)", line)
            if len(codes) >= 2:
                old, new = codes[:2]
                if old.startswith(("43", "83", "87")) and new.startswith("92"):
                    mapping[old] = new
                elif new.startswith(("43", "83", "87")) and old.startswith("92"):
                    mapping[new] = old
        return mapping
    header: list[str] = []
    rows = []
    for line in _lines(addedcode):
        if "|" not in line:
            header = line.rstrip(",").split(",")
            continue
        fields = line.split("|")
        rows.append({"market": fields[0], "old_code": fields[1], "new_code": fields[2],
                     "name": fields[3], "effective_date": fields[4] if len(fields) > 4 else "",
                     "raw_fields": fields})
    metadata = []
    for fields in _pipe_rows(bjmore, 4):
        metadata.append({"market": fields[0], "code": fields[1], "security_type": fields[2],
                         "name": fields[3], "status": fields[4] if len(fields) > 4 else "",
                         "raw_fields": fields})
    return {"header": header, "migrations": rows, "metadata": metadata}


def parse_tdxbjmore(data: bytes) -> list[dict[str, Any]]:
    rows = []
    for fields in _pipe_rows(data, 4):
        rows.append({"market": int(fields[0]), "code": fields[1], "name": fields[3], "kind": fields[2],
                     "source": "zhb_tdxbjmore", "fields": fields})
    return rows


def bj_rows_from_zhb(files: dict[str, bytes]) -> list[dict[str, Any]]:
    rows = parse_tdxbjmore(files.get("tdxbjmore.cfg", b""))
    mapping = parse_bj_mapping(files.get("addedcode_bj.cfg", b""))
    for row in rows:
        row["old_codes"] = [old for old, new in mapping.items() if new == row["code"]]
    return rows


def parse_ipo_subscriptions(xgsg: bytes, othersg: bytes = b"") -> dict[str, list[dict[str, Any]]]:
    """Parse equity IPO (``xgsg.cfg``) and other/bond subscription rows.

    Unknown positions are deliberately retained as ``field_N``.  The files do
    not label their columns, so only dates, codes, prices and names are
    exposed where their representation is unambiguous.
    """
    equity = []
    for fields in _pipe_rows(xgsg, 2):
        row = {f"field_{i}": value for i, value in enumerate(fields)}
        row.update({"market": fields[0], "code": fields[1], "subscription_date": fields[2] if len(fields) > 2 else "",
                    "price": _number(fields[3]) if len(fields) > 3 else None,
                    "name": fields[14] if len(fields) > 14 else "", "raw_fields": fields})
        equity.append(row)
    other = []
    for fields in _pipe_rows(othersg, 2):
        row = {f"field_{i}": value for i, value in enumerate(fields)}
        row.update({"market": fields[0], "stock_code": fields[1], "bond_code": fields[2] if len(fields) > 2 else "",
                    "issue_amount": _number(fields[3]) if len(fields) > 3 else None,
                    "price": _number(fields[4]) if len(fields) > 4 else None,
                    "subscription_date": fields[8] if len(fields) > 8 else "",
                    "name": fields[11] if len(fields) > 11 else "", "raw_fields": fields})
        other.append(row)
    return {"equity": equity, "other": other}


def parse_industry_definitions(data: bytes) -> list[dict[str, Any]]:
    """Parse ``incon.dat``'s code/name hierarchy (``#ZJHHY`` tree)."""
    rows = []
    for fields in _pipe_rows(data, 2):
        code, name = fields[:2]
        parent = None
        for index in range(len(code) - 1, 0, -1):
            candidate = code[:index]
            if any(existing["code"] == candidate for existing in rows):
                parent = candidate
                break
        rows.append({"code": code, "name": name, "level": len(code), "parent_code": parent,
                     "raw_fields": fields})
    return rows


def parse_industry_stock_references(data: bytes) -> list[dict[str, Any]]:
    """Parse a supplied ``tdxhy.cfg``-style stock/industry mapping.

    TDX builds differ in separators; this keeps all fields and recognizes a
    six-digit stock code followed by one or more industry codes.
    """
    rows = []
    for fields in _pipe_rows(data, 2):
        code = next((v for v in fields if re.fullmatch(r"\d{6}", v)), "")
        industries = [v for v in fields if re.fullmatch(r"[A-Z]\d{0,4}", v)]
        rows.append({"stock_code": code, "industry_codes": industries, "raw_fields": fields})
    return rows


def parse_tipinfo(data: bytes) -> list[dict[str, Any]]:
    """Parse 22-column per-stock report/rights metadata from ``tipinfo.dat``.

    Columns 0-3 are market, code, report period and EPS.  Column 4 is a
    plausible first-disclosure date, not an available-at timestamp; all
    remaining positions stay named ``field_N`` pending an independent schema.
    """
    rows = []
    for fields in _pipe_rows(data, 4):
        row = {f"field_{i}": value for i, value in enumerate(fields)}
        row.update({"market": fields[0], "code": fields[1], "report_period": fields[2],
                    "eps": _number(fields[3]), "announcement_date_candidate": fields[4] if len(fields) > 4 else "",
                    "raw_fields": fields})
        rows.append(row)
    return rows


def parse_importzs(data: bytes) -> list[dict[str, Any]]:
    """Parse ``importzs.cfg`` index-fund constituent/weight rows."""
    rows = []
    for fields in _pipe_rows(data, 2):
        numbers = [_number(v) for v in fields[2:]]
        rows.append({"code": fields[0], "date": fields[1], "raw_values": numbers, "raw_fields": fields})
    return rows


def parse_broker_companies(data: bytes) -> list[dict[str, Any]]:
    """Parse broker id, short name and legal name from ``brkcomp.dat``."""
    return [{"broker_id": fields[0], "short_name": fields[1], "legal_name": fields[2], "raw_fields": fields}
            for fields in _pipe_rows(data, 3)]


def parse_broker_seats(data: bytes) -> list[dict[str, Any]]:
    """Parse ``brkseat.dat`` triples (broker id, market/type, seat code)."""
    return [{"broker_id": fields[0], "seat_type": fields[1], "seat_code": fields[2], "raw_fields": fields}
            for fields in _pipe_rows(data, 3)]


def parse_chain_boards(data: bytes) -> list[dict[str, Any]]:
    """Parse board-code to chain/category-code/name rows from ``tdxchain.cfg``."""
    return [{"board_code": fields[0], "chain_code": fields[1], "name": fields[2], "raw_fields": fields}
            for fields in _pipe_rows(data, 3)]


def parse_adr_ah_pairs(adr: bytes, ah: bytes) -> dict[str, list[dict[str, Any]]]:
    """Parse ADR mapping and AH pair rows; ratio column is retained as raw."""
    adr_rows = [{"name": f[0], "hk_code": f[1], "adr_code": f[2], "ratio_or_type": f[3], "raw_fields": f}
                for f in _pipe_rows(adr, 4)]
    ah_rows = [{"name": f[0], "a_code": f[1], "hk_code": f[2], "ratio_or_type": f[3], "raw_fields": f}
               for f in _pipe_rows(ah, 4)]
    return {"adr": adr_rows, "ah": ah_rows}


def parse_pttab(data: bytes) -> list[dict[str, Any]]:
    """Parse retired/placeholder stock table rows (market, code, name)."""
    return [{"market": f[0], "code": f[1], "name": f[2], "raw_fields": f}
            for f in (line.split(",") for line in _lines(data)) if len(f) >= 3]


def parse_hspy(data: bytes) -> list[dict[str, Any]]:
    """Parse ``hspy.dat`` market, stock code and abbreviated name rows."""
    return [{"market": f[0], "code": f[1], "abbreviation": f[2], "raw_fields": f}
            for f in _pipe_rows(data, 3)]


def inspect_binary_member(data: bytes) -> dict[str, Any]:
    """Return conservative structure evidence for opaque TDX binary members."""
    prefix = data[:16]
    return {"size": len(data), "common_prefix_hex": prefix.hex(), "prefix_length": 16,
            "unique_bytes": len(set(data)), "zero_bytes": data.count(b"\x00"),
            "likely_compressed_or_encrypted": len(set(data[16:])) > 200 and data[16:].count(b"\x00") < max(32, len(data[16:]) // 50),
            "semantic_status": "UNKNOWN; no decoder claimed"}


__all__ = [name for name in globals() if name.startswith("parse_") or name in {"decode_text", "inspect_binary_member"}]
