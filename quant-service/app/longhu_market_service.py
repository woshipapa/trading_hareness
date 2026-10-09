"""Orchestrate one idempotent Longhu/Tencent post-close market refresh."""

from __future__ import annotations

import hashlib
import json
import math
from datetime import date, datetime, timezone
from typing import Any, Awaitable, Callable

from psycopg.types.json import Json

from .longhu_market_repository import persist_full_market_close, persist_settled_trade_calendar
from .longhu_market_sync import PROVIDER_KEY, merge_cross_section
from .longhu_vendor_source import LonghuVendorSource, direct_access_enabled


MINIMUM_ROWS = 3_500
MINIMUM_COVERAGE = 0.95
FULL_MARKET_SOURCE_TIMEOUT_SECONDS = 900


def minimum_full_market_rows(expected_rows: int) -> int:
    """Return the point-in-time all-A row gate for a provider response."""
    expected = max(int(expected_rows or 0), 0)
    return max(MINIMUM_ROWS, math.ceil(expected * MINIMUM_COVERAGE)) if expected else MINIMUM_ROWS


def owner_longhu_source_factory() -> LonghuVendorSource:
    """Construct the direct adapter only inside the licensed owner runtime."""
    if not direct_access_enabled():
        raise RuntimeError(
            "Longhu full-market vendor sync is owner-only; use the shared gateway from a peer"
        )
    return LonghuVendorSource()


async def sync(
    trade_date: date,
    *,
    db: Any,
    run_public_blocking: Callable[..., Awaitable[Any]],
    run_database_blocking: Callable[..., Awaitable[Any]],
    persist_rows: Callable[..., int],
    persist_flow_rows: Callable[..., int],
    source_factory: Callable[[], LonghuVendorSource] = owner_longhu_source_factory,
    force: bool = False,
) -> dict[str, Any]:
    request_key = hashlib.sha256(json.dumps({
        "capability": "longhu_full_market_close_v3", "trade_date": str(trade_date),
        "minimum_rows": MINIMUM_ROWS, "minimum_coverage": MINIMUM_COVERAGE,
    }, sort_keys=True).encode("utf-8")).hexdigest()

    def prepare() -> dict[str, Any]:
        with db.transaction() as connection:
            # The request roster is the owner's own equity universe, not the
            # vendor's industry classification.  Read it here, inside the
            # transaction that already exists, so the fetch cannot be bounded
            # by whatever the vendor happened to classify that morning.
            universe = [
                str(row["symbol"]) for row in connection.execute(
                    """SELECT symbol FROM quant.universe_members
                        WHERE universe_key='all_a' AND enabled ORDER BY symbol""",
                ).fetchall()
            ]
            prior = connection.execute(
                "SELECT status,row_count,metadata FROM quant.fetch_runs WHERE request_key=%s", (request_key,),
            ).fetchone()
            if prior and prior["status"] == "completed" and not force:
                # Older completed receipts can predate derived calendar
                # projection.  Repair that deterministic local artifact even
                # when no provider call is needed.
                calendar_rows = persist_settled_trade_calendar(
                    connection, trade_date, datetime.now(timezone.utc),
                )
                return {"unchanged": {
                    "status": "unchanged", "trade_date": str(trade_date),
                    "imported": int(prior["row_count"] or 0), "request_key": request_key,
                    "provider": PROVIDER_KEY, "metadata": prior["metadata"],
                    "calendar_rows": calendar_rows,
                }, "universe": universe}
            connection.execute(
                """INSERT INTO quant.fetch_runs(
                       provider_key,capability,trade_date,request_key,status,attempt_count,started_at,metadata)
                   VALUES(%s,'daily_all_a',%s,%s,'running',1,now(),%s)
                   ON CONFLICT(request_key) DO UPDATE SET status='running',
                     attempt_count=quant.fetch_runs.attempt_count+1,started_at=now(),finished_at=NULL,
                     error_class=NULL,error_message=NULL""",
                (PROVIDER_KEY, trade_date, request_key, Json({
                    "source": "longhuvip_industry_plus_dated_licensed_ohlc",
                    "physical_vendor_page_limit": 300,
                    "request_roster": "quant.universe_members:all_a",
                    "request_roster_symbols": len(universe),
                })),
            )
        return {"unchanged": None, "universe": universe}

    prepared = await run_database_blocking(prepare)
    if prepared["unchanged"]:
        return prepared["unchanged"]
    try:
        source = source_factory()
        evidence = await run_public_blocking(
            source.fetch_full_market_evidence, trade_date, prepared["universe"],
            timeout_seconds=FULL_MARKET_SOURCE_TIMEOUT_SECONDS,
        )
        merged = merge_cross_section(trade_date, evidence["vendor_rows"], evidence["quote_rows"])
        vendor_count = len(evidence["vendor_rows"])
        if vendor_count < MINIMUM_ROWS:
            raise RuntimeError(f"Longhu returned {vendor_count} symbols; minimum is {MINIMUM_ROWS}")
        expected_rows = await run_database_blocking(
            lambda: _expected_all_a_rows(db, trade_date), timeout_seconds=30,
        )
        minimum_rows = minimum_full_market_rows(expected_rows)
        if len(merged.daily_rows) < minimum_rows or merged.coverage < MINIMUM_COVERAGE:
            raise RuntimeError(
                f"cross-source OHLC coverage {len(merged.daily_rows)}/{vendor_count} "
                f"({merged.coverage:.2%}) is below the point-in-time all-A gate "
                f"{minimum_rows}/{expected_rows} (and {MINIMUM_COVERAGE:.0%} vendor coverage)"
            )
        observed_at = datetime.now(timezone.utc)

        def persist() -> dict[str, Any]:
            with db.transaction() as connection:
                return persist_full_market_close(
                    connection, trade_date=trade_date, request_key=request_key,
                    observed_at=observed_at, merged=merged, source_health=evidence["health"],
                    board_rows=evidence["board_rows"],
                    persist_rows=persist_rows, persist_flow_rows=persist_flow_rows,
                )

        persisted = await run_database_blocking(persist, timeout_seconds=240)
        return {
            "status": "completed", "trade_date": str(trade_date), "provider": PROVIDER_KEY,
            "request_key": request_key, **persisted, "source_health": evidence["health"],
            "semantic_boundary": (
                "main_net is vendor order-size classification; not institution identity or Level-2 order cancellation"
            ),
        }
    except Exception as error:
        error_type_name = type(error).__name__
        error_text = str(error)[:1000]
        def fail() -> None:
            with db.transaction() as connection:
                connection.execute(
                    """UPDATE quant.fetch_runs SET status='failed',finished_at=now(),
                              error_class=%s,error_message=%s
                        WHERE request_key=%s""",
                    (error_type_name, error_text, request_key),
                )
        await run_database_blocking(fail)
        return {
            "status": "failed", "trade_date": str(trade_date), "provider": PROVIDER_KEY,
            "request_key": request_key, "reason": f"{error_type_name}: {error_text}",
        }


def _expected_all_a_rows(db: Any, trade_date: date) -> int:
    """Read the point-in-time all-A population before promoting a close."""
    with db.transaction() as connection:
        row = connection.execute(
            """SELECT count(DISTINCT symbol)::int AS expected_rows
                 FROM quant.universe_membership_history
                WHERE universe_key='all_a' AND effective_from<=%s
                  AND (effective_to IS NULL OR effective_to>%s)""",
            (trade_date, trade_date),
        ).fetchone()
    return int((row or {}).get("expected_rows") or 0)


__all__ = [
    "FULL_MARKET_SOURCE_TIMEOUT_SECONDS", "MINIMUM_COVERAGE", "MINIMUM_ROWS",
    "minimum_full_market_rows", "sync",
]
