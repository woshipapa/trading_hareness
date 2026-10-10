"""Adapters for TDX's post-close ``zhb.zip`` and board files.

The files are snapshots: their row dates are effective dates and the adapter
observation time is the only availability clock.  Unknown columns remain in
``fields`` and are not promoted to canonical strategy fields.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timezone
from typing import Any

from ..contracts import CapabilityEvidence
from ..http import ashare_symbol
from . import tdx_files, tdx_protocol
from .tdx_zhb_extras import parse_holiday_calendar, parse_ipo_subscriptions


_TAXONOMIES = {4: "tdx_files_concept", 5: "tdx_files_style_event"}

_VALUATION_FIELDS = ("pe_ttm", "pe_static", "dividend_yield_pct")
_DAILY_BASIC_STAT_FIELDS = (
    "change_pct", "change_prev_day_pct", "change_prev2_day_pct", "streak", "change_4d_pct", "change_5d_pct",
    "change_10d_pct", "change_20d_pct", "change_60d_pct", "change_ytd_pct", "annual_limit_up_days",
    "circulating_capital_z_raw",
)
_DAILY_BASIC_STAT2_FIELDS = (
    "amount_10k_yuan", "amount_prev_10k_yuan", "amount_prev2_10k_yuan", "change_mtd_pct", "change_1y_pct",
    "auction_amount_10k_yuan", "high_52w_yuan", "low_52w_yuan",
)


def _symbol(market: int, code: str) -> str:
    return tdx_protocol.symbol(market, code)


def _symbol_from_code(code: str) -> str:
    suffix = "SH" if code.startswith(("6", "68")) else "BJ" if code.startswith(("4", "8", "9")) else "SZ"
    return f"{code}.{suffix}"


def _selected(symbols: Sequence[str], row: dict[str, Any]) -> bool:
    return not symbols or _symbol(row["market"], row["code"]) in symbols


def _project(row: dict[str, Any], names: Sequence[str]) -> dict[str, Any]:
    return {"symbol": _symbol(row["market"], row["code"]), "effective_date": row["date"],
            **{name: row[name] for name in names}}


async def _zip_files() -> tuple[dict[str, bytes], str]:
    return await tdx_protocol.call(lambda client: tdx_files.parse_zhb_zip(tdx_files.download(client, "zhb.zip")),
                                   handshake_profile="login_one")


async def fetch_valuation(*, symbols: Sequence[str] = ()) -> CapabilityEvidence:
    files, host = await _zip_files()
    rows = [_project(row, _VALUATION_FIELDS) for row in tdx_files.parse_tdxstat(files["tdxstat.cfg"])
            if _selected(symbols, row)]
    return tdx_protocol.observed_evidence(rows, host)


async def fetch_daily_basic(*, symbols: Sequence[str] = ()) -> CapabilityEvidence:
    files, host = await _zip_files()
    statistics = {(row["market"], row["code"], row["date"]): row for row in tdx_files.parse_tdxstat(files["tdxstat.cfg"])}
    rows = []
    for row in tdx_files.parse_tdxstat2(files["tdxstat2.cfg"]):
        stat = statistics.get((row["market"], row["code"], row["date"]))
        if stat is None or not _selected(symbols, row):
            continue
        rows.append({"symbol": _symbol(row["market"], row["code"]), "effective_date": row["date"],
                     **{name: stat[name] for name in _DAILY_BASIC_STAT_FIELDS},
                     **{name: row[name] for name in _DAILY_BASIC_STAT2_FIELDS}})
    return tdx_protocol.observed_evidence(rows, host)


def _membership_rows(blocks: Sequence[dict[str, Any]], definitions: dict[str, dict[str, Any]], expected_type: int,
                     known_at: datetime) -> tuple[list[dict[str, Any]], list[str], list[str], int]:
    rows: list[dict[str, Any]] = []
    joined: set[str] = set()
    unmatched: set[str] = set()
    rejected = 0
    for block in blocks:
        definition = definitions.get(block["name"])
        if definition is None or definition["type"] != expected_type:
            unmatched.add(block["name"])
            continue
        joined.add(block["name"])
        taxonomy_key = _TAXONOMIES[expected_type]
        for code in block["members"]:
            symbol_value = ashare_symbol(code)
            if symbol_value is None:
                rejected += 1
                continue
            rows.append({"taxonomy_key": taxonomy_key, "sector_key": definition["code"],
                         "sector_name": block["name"], "board_type": definition["type"],
                         "symbol": symbol_value, "known_at": known_at})
    return rows, sorted(joined), sorted(unmatched), rejected


async def fetch_membership() -> CapabilityEvidence:
    collection_time = datetime.now(timezone.utc)

    def read(client: tdx_protocol.TdxClient) -> tuple[list[dict[str, Any]], list[str], list[str], int]:
        files = tdx_files.parse_zhb_zip(tdx_files.download(client, "zhb.zip"))
        definitions = {row["name"]: row for row in tdx_files.parse_tdxzs(files["tdxzs3.cfg"])}
        rows: list[dict[str, Any]] = []
        joined: list[str] = []
        unmatched: list[str] = []
        rejected = 0
        for filename, expected_type in (("block_gn.dat", 4), ("block_fg.dat", 5)):
            family_rows, family_joined, family_unmatched, family_rejected = _membership_rows(
                tdx_files.parse_block_file(tdx_files.download(client, filename)), definitions, expected_type, collection_time)
            rows.extend(family_rows)
            joined.extend(f"{filename}:{name}" for name in family_joined)
            unmatched.extend(f"{filename}:{name}" for name in family_unmatched)
            rejected += family_rejected
        return rows, joined, unmatched, rejected

    (rows, joined, unmatched, rejected), host = await tdx_protocol.call(read, handshake_profile="login_one")
    warnings = (f"joined_boards={','.join(joined)}", f"unmatched_boards={','.join(unmatched)}",
                f"rejected_members={rejected}", "block_zs.dat and spblock.dat excluded: index/special lists are not sectors")
    return tdx_protocol.observed_evidence(rows, host, warnings=warnings)


async def fetch_trade_calendar() -> CapabilityEvidence:
    files, host = await _zip_files()
    calendar = parse_holiday_calendar(files["needini.dat"], files["hqrule.dat"])
    rows = [{"exchange": "CN", "calendar_date": day, "is_open": False, "row_type": "holiday",
             "source_file": "needini.dat"}
            for day in calendar["holidays"]]
    return tdx_protocol.observed_evidence(
        rows, host, warnings=(f"declared_year_count={calendar['declared_year_count']}",
                              "rows=declared holidays only; open days are not fabricated"))


async def fetch_ipo_calendar() -> CapabilityEvidence:
    files, host = await _zip_files()
    parsed = parse_ipo_subscriptions(files["xgsg.cfg"], files["othersg.cfg"])
    rows = []
    for item in parsed["equity"]:
        rows.append({"symbol": _symbol(int(item["market"]), item["code"]), "apply_date": item["subscription_date"],
                     "issue_price": item["price"], "event_type": "equity_subscription", "source_file": "xgsg.cfg",
                     "raw_fields": item["raw_fields"]})
    for item in parsed["other"]:
        rows.append({"symbol": _symbol(int(item["market"]), item["stock_code"]), "apply_date": item["subscription_date"],
                     "issue_price": item["price"], "event_type": "other_subscription", "source_file": "othersg.cfg",
                     "bond_code": item["bond_code"], "raw_fields": item["raw_fields"]})
    return tdx_protocol.observed_evidence(rows, host, warnings=("rows=declared subscription dates; no listing/open dates inferred",))


__all__ = ["fetch_daily_basic", "fetch_ipo_calendar", "fetch_membership", "fetch_trade_calendar", "fetch_valuation"]
