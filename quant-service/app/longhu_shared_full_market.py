"""Full-market close evidence for a peer, through the owner's gateway.

``LonghuVendorSource.fetch_full_market_evidence`` is owner-only: it holds the
upstream credential and refuses to construct anywhere else.  Its own refusal
names the alternative - "use the shared gateway from a peer" - but nothing
implemented it, so a peer asking for the licensed close cross-section fell back
to the Tushare REST backup.  That route is rated at six requests per minute
against a local wait budget of five seconds, which a three-page full-market
fetch cannot clear: the 2026-09-18 close never landed.

This assembles the same evidence from the same vendor actions, reached through
the gateway that already serves this peer's board flow and sector membership.
The credential still never leaves the owner.  Only the transport differs, so
the rows, the merge and everything persisted downstream are unchanged.

Post-close only: the member action's live parameter form is rejected by the
vendor outright, so membership and close rows are read in the historical form,
for a session that has finished.
"""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from typing import Any, Iterable, Mapping, NoReturn

import requests

from .longhu_board_flow import gateway_rows as board_gateway_rows
from .longhu_sector_membership import (
    CATALOG_MAX_PAGES,
    MEMBER_PAGE_SIZE,
    catalog_request,
    gateway_count,
    member_request,
)
from .longhu_vendor_source import (
    SharedLonghuReadSource,
    parse_industry_stock_row,
    tencent_quote_batches,
)

#: The vendor publishes about 104 industry boards.  Fewer than this means a
#: partial catalog, and a partial catalog silently shrinks the market.
MINIMUM_PLATES = 90

#: Member pages are fetched concurrently, but a peer shares one gateway with
#: the intraday capture loop, so the default stays well below the owner's.
DEFAULT_WORKERS = 4

MEMBER_MAX_PAGES = 10


def _number(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed == parsed else None


def gateway_workers(environ: Mapping[str, str] | None = None) -> int:
    env = os.environ if environ is None else environ
    try:
        return max(1, min(16, int(env.get("QUANT_LONGHU_GATEWAY_WORKERS", str(DEFAULT_WORKERS)))))
    except (TypeError, ValueError):
        return DEFAULT_WORKERS


def parse_catalog_row(row: Any) -> dict[str, Any] | None:
    """One ranking row in the shape the owner adapter produces."""
    if not isinstance(row, (list, tuple)) or not row:
        return None
    sector_key = str(row[0] or "").strip()
    if not sector_key:
        return None
    return {
        "sector_key": sector_key,
        "label": str(row[1] if len(row) > 1 else sector_key),
        "strength": _number(row[2]) if len(row) > 2 else None,
        "change_pct": _number(row[3]) if len(row) > 3 else None,
        "speed": _number(row[4]) if len(row) > 4 else None,
        "amount": _number(row[5]) if len(row) > 5 else None,
        "net_inflow": _number(row[6]) if len(row) > 6 else None,
        "volume_ratio": _number(row[9]) if len(row) > 9 else None,
        "taxonomy_key": "longhu_ths_industry",
    }


class SharedLonghuFullMarketSource:
    """The owner adapter's full-market contract, served over the gateway."""

    def __init__(self, read_source: Any | None = None, *, workers: int | None = None,
                 session: Any | None = None, timeout_seconds: float = 30.0) -> None:
        self._source = read_source or SharedLonghuReadSource()
        self._workers = workers or gateway_workers()
        self._session = session or requests.Session()
        self._timeout_seconds = timeout_seconds

    def industry_plate_catalog(self) -> list[dict[str, Any]]:
        """Every industry board, paged until the vendor's own Count is reached."""
        result: list[dict[str, Any]] = []
        seen: set[str] = set()
        expected: int | None = None
        offset = 0
        for _ in range(CATALOG_MAX_PAGES):
            payload = self._source.raw_call(catalog_request(offset))
            rows = board_gateway_rows(payload)
            expected = expected if expected is not None else gateway_count(payload)
            if not rows:
                break
            for row in rows:
                parsed = parse_catalog_row(row)
                if parsed and parsed["sector_key"] not in seen:
                    seen.add(parsed["sector_key"])
                    result.append(parsed)
            # This endpoint returns its own page size whatever is requested, so
            # the cursor advances by what arrived.
            offset += len(rows)
            if expected is not None and len(result) >= expected:
                break
        if len(result) < MINIMUM_PLATES:
            raise RuntimeError(f"Longhu industry coverage too small: {len(result)}")
        return result

    def plate_day(self, plate_id: str, trade_date: date) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        seen: set[str] = set()
        expected: int | None = None
        offset = 0
        for _ in range(MEMBER_MAX_PAGES):
            payload = self._source.raw_call(member_request(plate_id, trade_date, offset))
            rows = board_gateway_rows(payload)
            expected = expected if expected is not None else gateway_count(payload)
            if not rows:
                break
            for row in rows:
                parsed = parse_industry_stock_row(row, trade_date, plate_id)
                if parsed and parsed["symbol"] not in seen:
                    seen.add(parsed["symbol"])
                    result.append(parsed)
            offset += len(rows)
            if len(rows) < MEMBER_PAGE_SIZE or (expected is not None and len(result) >= expected):
                break
        return result

    def full_market_vendor_rows(
        self, trade_date: date, *, plate_ids: Iterable[str] | None = None,
    ) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
        """Union the boards' members, reporting coverage rather than hiding gaps."""
        plates = list(plate_ids) if plate_ids is not None else [
            row["sector_key"] for row in self.industry_plate_catalog()
        ]
        by_symbol: dict[str, dict[str, Any]] = {}
        conflicts: list[dict[str, Any]] = []
        errors: list[dict[str, str]] = []
        successful = 0
        if plates:
            with ThreadPoolExecutor(max_workers=min(self._workers, len(plates)),
                                    thread_name_prefix="longhu-gateway") as pool:
                futures = {pool.submit(self.plate_day, plate, trade_date): plate for plate in plates}
                for future in as_completed(futures):
                    plate = futures[future]
                    try:
                        rows = future.result()
                    except Exception as error:  # noqa: BLE001 - one board is not the market
                        errors.append({"plate_id": plate, "error": f"{type(error).__name__}: {error}"})
                        continue
                    successful += 1
                    for row in rows:
                        existing = by_symbol.get(row["symbol"])
                        # A name carried by two boards must agree on its flow;
                        # disagreement is reported, never silently averaged.
                        if existing and existing["main_net"] != row["main_net"]:
                            conflicts.append({"symbol": row["symbol"], "plates": [existing["plate_id"], plate]})
                            continue
                        by_symbol[row["symbol"]] = row
        health = {
            "plates": len(plates), "successful_plates": successful,
            "plate_coverage": successful / len(plates) if plates else 0.0,
            "symbols": len(by_symbol), "errors": errors[:20],
            "duplicate_conflicts": conflicts[:20], "physical_page_limit": MEMBER_PAGE_SIZE,
            "transport": "shared_gateway",
        }
        return by_symbol, health

    def tencent_quotes(self, symbols: Iterable[str]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """Public OHLC, which needs no licence and so is fetched directly."""
        return tencent_quote_batches(self._session, symbols, timeout_seconds=self._timeout_seconds)

    def fetch_full_market_evidence(self, trade_date: date) -> dict[str, Any]:
        catalog = self.industry_plate_catalog()
        vendor, vendor_health = self.full_market_vendor_rows(
            trade_date, plate_ids=[row["sector_key"] for row in catalog],
        )
        quotes, quote_health = self.tencent_quotes(vendor)
        members_by_plate: dict[str, list[dict[str, Any]]] = {}
        for row in vendor.values():
            members_by_plate.setdefault(str(row["plate_id"]), []).append(row)
        board_rows: list[dict[str, Any]] = []
        for board in catalog:
            members = members_by_plate.get(board["sector_key"], [])
            leaders = sorted(
                members,
                key=lambda row: (float(row.get("main_net") or 0), float(row.get("pct_chg") or 0)),
                reverse=True,
            )[:10]
            board_rows.append({
                **board, "mapped_members": len(members), "quoted_members": len(members),
                "top_stocks": [{
                    "symbol": row["symbol"], "name": row["name"],
                    "pct_change": row.get("pct_chg"), "net_inflow": row.get("main_net"),
                } for row in leaders],
                "source": "longhuvip_gateway:RealRankingInfo+ZhiShuStockList_W8",
            })
        return {
            "trade_date": trade_date, "vendor_rows": vendor, "quote_rows": quotes,
            "board_rows": board_rows,
            "health": {"longhu": vendor_health, "tencent": quote_health},
        }


def shared_longhu_source_factory() -> SharedLonghuFullMarketSource:
    """Construct the gateway-backed adapter used when this host holds no licence."""
    return SharedLonghuFullMarketSource()


def owner_factor_task(*_args: Any, **_kwargs: Any) -> NoReturn:
    """Reject attempts to derive owner adjustment factors on the peer.

    The catalog keeps the owner-maintained ``longhu_qfq_derived`` binding
    visible for provenance, but this checkout is a gateway consumer.  Factor
    derivation requires the owner's Longhu close job and must be read back
    from ``quant.daily_adjustment_factors``; silently deriving a second series
    here would break the provider/availability contract.
    """
    raise RuntimeError(
        "longhu_qfq_derived is owner-only; peer must read the persisted factor task output"
    )


__all__ = [
    "DEFAULT_WORKERS", "MEMBER_MAX_PAGES", "MINIMUM_PLATES",
    "SharedLonghuFullMarketSource", "gateway_workers", "owner_factor_task",
    "parse_catalog_row", "shared_longhu_source_factory",
]
