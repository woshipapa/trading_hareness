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
from . import tdx_files, tdx_protocol
from .tdx_zhb_extras import parse_holiday_calendar, parse_ipo_subscriptions


_MARKET_SUFFIX = {"0": "SZ", "1": "SH", "2": "BJ"}
_TAXONOMIES = {2: "tdx_files_industry_l1", 3: "tdx_files_region", 4: "tdx_files_concept",
               5: "tdx_files_style_event", 12: "tdx_files_industry_l2_l3"}


def _symbol(market: int, code: str) -> str:
    return tdx_protocol.symbol(market, code)


def _symbol_from_code(code: str) -> str:
    suffix = "SH" if code.startswith(("6", "68")) else "BJ" if code.startswith(("4", "8", "9")) else "SZ"
    return f"{code}.{suffix}"


def _selected(symbols: Sequence[str], row: dict[str, Any]) -> bool:
    return not symbols or _symbol(row["market"], row["code"]) in symbols


def _stat_row(row: dict[str, Any]) -> dict[str, Any]:
    return {"symbol": _symbol(row["market"], row["code"]), "effective_date": row["date"],
            **{key: value for key, value in row.items() if key not in {"market", "code", "date"}}}


async def _zip_files() -> tuple[dict[str, bytes], str]:
    return await tdx_protocol.call(lambda client: tdx_files.parse_zhb_zip(tdx_files.download(client, "zhb.zip")),
                                   handshake_profile="login_one")


async def fetch_valuation(*, symbols: Sequence[str] = ()) -> CapabilityEvidence:
    files, host = await _zip_files()
    rows = [_stat_row(row) for row in tdx_files.parse_tdxstat(files["tdxstat.cfg"]) if _selected(symbols, row)]
    return tdx_protocol.observed_evidence(rows, host)


async def fetch_daily_basic(*, symbols: Sequence[str] = ()) -> CapabilityEvidence:
    files, host = await _zip_files()
    rows = [_stat_row(row) for row in tdx_files.parse_tdxstat2(files["tdxstat2.cfg"]) if _selected(symbols, row)]
    return tdx_protocol.observed_evidence(rows, host)


def _board_symbol(code: str) -> str:
    return _symbol_from_code(code) if len(code) == 6 else _symbol(int(code[0]), code[1:])


def _membership_rows(blocks: Sequence[dict[str, Any]], definitions: dict[str, dict[str, Any]], known_at: datetime) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    rows: list[dict[str, Any]] = []
    joined: set[str] = set()
    unmatched: set[str] = set()
    for block in blocks:
        definition = definitions.get(block["name"])
        if definition is None or definition["type"] not in _TAXONOMIES:
            unmatched.add(block["name"])
            continue
        joined.add(block["name"])
        taxonomy_key = _TAXONOMIES[definition["type"]]
        for code in block["members"]:
            rows.append({"taxonomy_key": taxonomy_key, "sector_key": definition["code"],
                         "sector_name": block["name"], "board_type": definition["type"],
                         "symbol": _board_symbol(code), "known_at": known_at})
    return rows, sorted(joined), sorted(unmatched)


async def fetch_membership() -> CapabilityEvidence:
    collection_time = datetime.now(timezone.utc)

    def read(client: tdx_protocol.TdxClient) -> tuple[list[dict[str, Any]], list[str], list[str]]:
        files = tdx_files.parse_zhb_zip(tdx_files.download(client, "zhb.zip"))
        blocks = [block for filename in ("block_gn.dat", "block_fg.dat", "block_zs.dat")
                  for block in tdx_files.parse_block_file(tdx_files.download(client, filename))]
        blocks.extend(tdx_files.parse_spblock(files["spblock.dat"]))
        definitions = {row["name"]: row for row in tdx_files.parse_tdxzs(files["tdxzs3.cfg"])}
        return _membership_rows(blocks, definitions, collection_time)

    (rows, joined, unmatched), host = await tdx_protocol.call(read, handshake_profile="login_one")
    warnings = (f"joined_boards={','.join(joined)}", f"unmatched_boards={','.join(unmatched)}")
    return tdx_protocol.observed_evidence(rows, host, warnings=warnings)


async def fetch_trade_calendar() -> CapabilityEvidence:
    files, host = await _zip_files()
    calendar = parse_holiday_calendar(files["needini.dat"], files["hqrule.dat"])
    rows = [{"exchange": "CN", "calendar_date": day, "is_open": False, "row_type": "holiday"}
            for day in calendar["holidays"]]
    return tdx_protocol.observed_evidence(rows, host, warnings=("rows=declared holidays only; open days are not fabricated",))


async def fetch_ipo_calendar() -> CapabilityEvidence:
    files, host = await _zip_files()
    parsed = parse_ipo_subscriptions(files["xgsg.cfg"], files["othersg.cfg"])
    rows = []
    for item in parsed["equity"]:
        rows.append({"symbol": _symbol(int(item["market"]), item["code"]), "apply_date": item["subscription_date"],
                     "issue_price": item["price"], "event_type": "equity_subscription", "raw_fields": item["raw_fields"]})
    for item in parsed["other"]:
        rows.append({"symbol": _symbol(int(item["market"]), item["stock_code"]), "apply_date": item["subscription_date"],
                     "issue_price": item["price"], "event_type": "other_subscription", "bond_code": item["bond_code"],
                     "raw_fields": item["raw_fields"]})
    return tdx_protocol.observed_evidence(rows, host, warnings=("rows=declared subscription dates; no listing/open dates inferred",))


__all__ = ["fetch_daily_basic", "fetch_ipo_calendar", "fetch_membership", "fetch_trade_calendar", "fetch_valuation"]
