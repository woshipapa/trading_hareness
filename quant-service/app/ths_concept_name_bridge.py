"""Join 同花顺 board flows, keyed by board name, to 同花顺 board codes.

The one-minute board-flow capture stores its rows as ``eastmoney_concept`` and
``eastmoney_industry``, but the akshare calls behind it
(``stock_fund_flow_concept``/``_industry``) read data.10jqka.com.cn: they are
同花顺 boards.  That table has no code column, so a stored ``sector_key`` is
the board's display name.  Membership is kept by code (``NNNNNN.TI``) under
``fuyao_ths_concept``/``fuyao_ths_industry``, and before 2026-10-08 under the
Tushare-era ``ths_concept_flow``/``ths_index_i``, with the same codes.

A flow therefore reaches its members only through the board's name.  Names are
compared after NFKC folding (full-width to half-width), with spaces and
brackets dropped and Latin letters lower-cased.  The Fuyao catalog is
consulted first and the frozen THS catalog second; a name two codes share in
the same catalog is ambiguous and left unmatched.  Every unmatched name is
reported, never silently dropped.  Pure: the catalog rows come from the
caller (``CATALOG_SQL``).
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

#: Catalog taxonomies by kind, live first.  The concept codes are one 同花顺
#: code space; an industry match names the catalog (and so the membership) it came from.
CATALOGS: dict[str, tuple[str, ...]] = {
    "concept": ("fuyao_ths_concept", "ths_concept_flow"),
    # Longhu's boards carry 同花顺 industry names under its own plate ids.
    "industry": ("longhu_ths_industry", "fuyao_ths_industry", "ths_index_i"),
}
#: The taxonomy each kind's name-keyed flow is stored under.
FLOW_TAXONOMIES = {"concept": "eastmoney_concept", "industry": "eastmoney_industry"}
#: Where the name-keyed flow actually comes from.
FLOW_SOURCE = "ths_10jqka_via_akshare"

CATALOG_SQL = """SELECT taxonomy_key,sector_key,label FROM quant.sectors
                  WHERE taxonomy_key = ANY(%s) AND label IS NOT NULL AND label<>''"""

_DROPPED = re.compile(r"[\s()\[\]{}<>【】「」『』〔〕〈〉《》]+")


def normalize_board_name(name: Any) -> str:
    return _DROPPED.sub("", unicodedata.normalize("NFKC", str(name or ""))).lower()


@dataclass(frozen=True)
class NameBridge:
    """Board name <-> 同花顺 code for one kind of board."""

    kind: str
    code_by_name: dict[str, str] = field(default_factory=dict)
    name_by_code: dict[str, str] = field(default_factory=dict)
    catalog_by_code: dict[str, str] = field(default_factory=dict)
    ambiguous: frozenset[str] = frozenset()

    def code_for(self, name: Any) -> str | None:
        return self.code_by_name.get(normalize_board_name(name))

    def name_for(self, code: Any) -> str | None:
        return self.name_by_code.get(str(code or "").strip().upper())

    def match(self, names: Iterable[Any]) -> tuple[dict[str, str], dict[str, Any]]:
        """``{name: code}`` for the names that match, and the coverage of the match."""
        matched: dict[str, str] = {}
        unmatched: list[str] = []
        ambiguous: list[str] = []
        for name in dict.fromkeys(str(item or "").strip() for item in names):
            if not name:
                continue
            code = self.code_for(name)
            if code is not None:
                matched[name] = code
            elif normalize_board_name(name) in self.ambiguous:
                ambiguous.append(name)
            else:
                unmatched.append(name)
        return matched, {
            "flow_boards": len(matched) + len(unmatched) + len(ambiguous), "matched": len(matched),
            "unmatched": sorted(unmatched), "ambiguous": sorted(ambiguous), "join": "board_name",
            "catalogs": list(CATALOGS[self.kind]),
        }


def build_bridge(kind: str, catalog_rows: Iterable[Mapping[str, Any]]) -> NameBridge:
    """Build the bridge from ``CATALOG_SQL`` rows; earlier catalogs in ``CATALOGS[kind]`` win."""
    order = {taxonomy: rank for rank, taxonomy in enumerate(CATALOGS[kind])}
    rows = sorted(
        (dict(row) for row in catalog_rows if str(dict(row).get("taxonomy_key")) in order),
        key=lambda row: (order[str(row["taxonomy_key"])], str(row["sector_key"])),
    )
    code_by_name: dict[str, str] = {}
    name_by_code: dict[str, str] = {}
    catalog_by_code: dict[str, str] = {}
    claimed_rank: dict[str, int] = {}
    ambiguous: set[str] = set()
    for row in rows:
        code = str(row["sector_key"]).strip().upper()
        label = str(row.get("label") or "").strip()
        key = normalize_board_name(label)
        rank = order[str(row["taxonomy_key"])]
        if not code or not key:
            continue
        name_by_code.setdefault(code, label)
        catalog_by_code.setdefault(code, str(row["taxonomy_key"]))
        if key in ambiguous:
            continue
        if key not in code_by_name:
            code_by_name[key], claimed_rank[key] = code, rank
        elif code_by_name[key] != code and claimed_rank[key] == rank:
            # Two codes of one catalog share the name: no honest answer.
            ambiguous.add(key)
            del code_by_name[key]
    return NameBridge(kind, code_by_name, name_by_code, catalog_by_code, frozenset(ambiguous))



#: Daily concept-flow features of both taxonomies, with the taxonomy each row is from.
CONCEPT_DAYS_SQL = """SELECT feature.taxonomy_key,feature.sector_key,sector.label,feature.net_amount,
                             feature.lhb_negative_count,feature.trading_date
                        FROM quant.sector_flow_daily_features feature JOIN quant.sectors sector
                          ON sector.taxonomy_key=feature.taxonomy_key AND sector.sector_key=feature.sector_key
                       WHERE feature.taxonomy_key IN ('eastmoney_concept','ths_concept_flow')
                         AND feature.trading_date BETWEEN %s AND %s
                         AND feature.status='ready' AND feature.available_at<=%s"""
#: Approved theme -> 同花顺 concept code aliases; the Fuyao taxonomy first, the same codes either way.
THEME_ALIASES_SQL = """SELECT theme_key,sector_key FROM quant.analyst_theme_board_aliases
                        WHERE status='approved' AND taxonomy_key IN ('fuyao_ths_concept','ths_concept_flow')
                        ORDER BY CASE taxonomy_key WHEN 'fuyao_ths_concept' THEN 0 ELSE 1 END,theme_key"""


def theme_board_codes(rows: Iterable[Mapping[str, Any]]) -> dict[str, str]:
    codes: dict[str, str] = {}
    for row in rows:
        codes.setdefault(str(row["theme_key"]), str(row["sector_key"]))
    return codes


def concept_days_by_code(rows: Iterable[Mapping[str, Any]], bridge: NameBridge) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Daily concept rows keyed by 同花顺 code.

    Name-keyed live rows (``eastmoney_concept``) are re-keyed through the
    bridge and replace a session's frozen ``ths_concept_flow`` rows; a live
    board whose name does not match is left out and counted.
    """
    items = [dict(row) for row in rows]
    live_sessions = {row["trading_date"] for row in items if row.get("taxonomy_key") == FLOW_TAXONOMIES["concept"]}
    keyed: list[dict[str, Any]] = []
    unmatched: set[str] = set()
    for row in items:
        if row.get("taxonomy_key") == FLOW_TAXONOMIES["concept"]:
            code = bridge.code_for(row["sector_key"])
            if code is None:
                unmatched.add(str(row["sector_key"]))
                continue
            keyed.append({**row, "sector_key": code, "flow_board_name": row["sector_key"]})
        elif row["trading_date"] not in live_sessions:
            keyed.append(row)
    return keyed, {
        "taxonomies": [FLOW_TAXONOMIES["concept"], "ths_concept_flow"], "live_sessions": len(live_sessions),
        "flow_source": FLOW_SOURCE, "join": "board_name_to_ths_code",
        "unmatched_count": len(unmatched), "unmatched": sorted(unmatched)[:50],
    }

__all__ = [
    "CATALOGS", "CATALOG_SQL", "CONCEPT_DAYS_SQL", "FLOW_SOURCE", "FLOW_TAXONOMIES", "NameBridge", "THEME_ALIASES_SQL",
    "build_bridge", "concept_days_by_code", "normalize_board_name", "theme_board_codes",
]
