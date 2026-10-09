"""Cadence-only capture of the complete Fuyao A-share Level-1 cross-section.

The collector stores provider rows as raw evidence.  Ranking, width and
strategy eligibility remain downstream research projections; this loop never
creates a trading decision.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from . import minute_cross_section


async def capture_level1_snapshot(
    *,
    fetch_snapshot: Callable[[], Awaitable[tuple[list[dict[str, Any]], Mapping[str, Any]]]],
    persist: Callable[[str, str, list[dict[str, Any]]], Awaitable[int]],
    session_open: Callable[[datetime], Awaitable[bool]],
    persist_health: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    now: datetime | None = None,
    on_persisted: Callable[[datetime, list[dict[str, Any]]], Awaitable[dict[str, Any]]] | None = None,
    persist_document: Callable[[datetime, list[dict[str, Any]], Mapping[str, Any]], Awaitable[dict[str, Any]]] | None = None,
    storage: str = "per_symbol",
) -> dict[str, Any]:
    """Capture one all-A snapshot, returning a secret-free health result.

    ``storage`` (decision 0009): ``per_symbol`` writes a row per stock, ``document``
    one row for the minute, ``both`` while readers move over. With ``document``
    alone a failed document write fails the capture; with ``both`` it is
    reported and the per-symbol rows still stand.

    ``on_persisted`` derives from the stored cross-section (the market radar);
    its failure is reported in the result and never fails the capture.
    """
    observed_at = now or datetime.now(timezone.utc)
    # Some application session adapters return ``(active, reason)`` while the
    # collector contract historically accepted a bare bool.  Normalize both
    # forms here so a false tuple cannot be treated as truthy and accidentally
    # trigger a provider request outside the exchange session.
    session_result = await session_open(observed_at)
    active = bool(session_result[0]) if isinstance(session_result, tuple) else bool(session_result)
    if not active:
        return {"status": "outside_session", "received": 0, "stored": 0}
    rows, metadata = await fetch_snapshot()
    payloads: list[dict[str, Any]] = []
    for row in rows:
        symbol = str(row.get("symbol") or row.get("ts_code") or "").upper()
        if not symbol:
            continue
        payloads.append({
            **dict(row),
            "ts_code": symbol,
            "snapshot_observed_at": observed_at.isoformat(),
            "snapshot_metadata": dict(metadata),
            "research_only": True,
        })
    stored = 0
    timings: dict[str, float] = {}
    if payloads and storage in ("per_symbol", "both"):
        started = asyncio.get_running_loop().time()
        stored = await persist("fuyao_ths", "a_share_prices_snapshot", payloads)
        timings["per_symbol_seconds"] = round(asyncio.get_running_loop().time() - started, 3)
    document: dict[str, Any] | None = None
    document_error: str | None = None
    if payloads and storage in ("document", "both") and persist_document is not None:
        started = asyncio.get_running_loop().time()
        try:
            document = await persist_document(observed_at, payloads, metadata)
            timings["document_seconds"] = round(asyncio.get_running_loop().time() - started, 3)
        except Exception as error:  # noqa: BLE001 - reported; fatal only when it is the only copy
            if storage == "document":
                raise
            document_error = f"{type(error).__name__}: {str(error)[:200]}"
        if document is not None:
            stored = stored or int(document.get("rows") or 0)
    if payloads:
        minute_cross_section.remember(observed_at, payloads)
    result = {
        "status": "completed" if payloads else "empty",
        "received": len(payloads),
        "stored": stored,
        "provider": "fuyao_ths",
        "capability": "a_share_prices_snapshot",
        "upstream_timestamp_ms": metadata.get("upstream_timestamp_ms"),
        "freshness_status": metadata.get("status") or metadata.get("freshness_status") or "unknown",
        "cross_sectional": bool(metadata.get("cross_sectional", False)),
        "storage": storage, "timings": timings,
        "document": document, "document_error": document_error,
    }
    if on_persisted is not None and payloads:
        try:
            result["derived"] = await on_persisted(observed_at, payloads)
        except Exception as error:  # noqa: BLE001 - a derived view must not cost the evidence
            result["derived"] = {"status": "failed", "error": f"{type(error).__name__}: {str(error)[:200]}"}
    if persist_health is not None:
        await persist_health(result)
    return result


async def run_level1_snapshot_loop(
    *,
    interval_seconds: int,
    capture: Callable[[], Awaitable[dict[str, Any]]],
    log: Callable[[str], None] = print,
) -> None:
    """Run at a bounded ~60 second cadence; errors never stop future rounds."""
    while True:
        try:
            result = await capture()
            if result.get("status") not in {"completed", "empty", "outside_session"}:
                log(f"all-A Level-1 capture degraded: {str(result)[:400]}")
        except Exception as error:  # noqa: BLE001 - next cadence is the retry
            log(f"all-A Level-1 capture failed: {str(error)[:300]}")
        await asyncio.sleep(max(10, min(300, int(interval_seconds))))


@dataclass(frozen=True)
class Level1CaptureDependencies:
    """What the minute capture needs from the rest of the service."""

    fetch_snapshot: Callable[[], Awaitable[tuple[list[dict[str, Any]], Mapping[str, Any]]]]
    persist_observations: Callable[..., int]
    run_database: Callable[..., Awaitable[Any]]
    database: Any
    session_open: Callable[[datetime], Awaitable[tuple[bool, str]]]
    record_success: Callable[..., Any]
    record_failure: Callable[..., Any]
    safe_error: Callable[[str, int], str]
    #: Derives from each stored cross-section (the market radar); optional.
    on_persisted: Callable[[datetime, list[dict[str, Any]]], Awaitable[dict[str, Any]]] | None = None


def level1_capture(deps: Level1CaptureDependencies) -> Callable[[], Awaitable[dict[str, Any]]]:
    """One capture round that also records the provider's health, failures included."""
    async def persist(provider: str, capability: str, rows: list[dict[str, Any]]) -> int:
        return await deps.run_database(deps.persist_observations, provider, capability, rows, timeout_seconds=90)

    async def session_open(now: datetime) -> bool:
        active, _reason = await deps.session_open(now)
        return active

    async def persist_health(result: dict[str, Any]) -> None:
        status = str(result.get("status") or "unknown")
        if status == "outside_session":
            return
        error = str(result.get("error") or f"all-A snapshot status={status}")

        def write() -> None:
            with deps.database.transaction() as connection:
                if status == "completed" and int(result.get("received") or 0) > 0:
                    deps.record_success(connection, "fuyao_ths", "a_share_prices_snapshot",
                                        int(result.get("received") or 0), None)
                else:
                    deps.record_failure(connection, "fuyao_ths", "a_share_prices_snapshot", error, None)

        await deps.run_database(write, timeout_seconds=10)

    async def persist_document(observed_at: datetime, rows: list[dict[str, Any]],
                               metadata: Mapping[str, Any]) -> dict[str, Any]:
        return await deps.run_database(minute_cross_section.persist_document, deps.database, observed_at, rows,
                                       metadata, timeout_seconds=60)

    async def capture() -> dict[str, Any]:
        try:
            return await capture_level1_snapshot(fetch_snapshot=deps.fetch_snapshot, persist=persist,
                                                 persist_health=persist_health, session_open=session_open,
                                                 on_persisted=deps.on_persisted, persist_document=persist_document,
                                                 storage=minute_cross_section.storage_mode())
        except Exception as error:  # noqa: BLE001 - health must see provider errors
            await persist_health({"status": "failed", "error": deps.safe_error(str(error), 300)})
            raise

    return capture


async def run_level1_capture_service(deps: Level1CaptureDependencies) -> None:
    """Persist one complete all-A Level-1 cross-section about every minute."""
    await run_level1_snapshot_loop(interval_seconds=60, capture=level1_capture(deps))


__all__ = [
    "Level1CaptureDependencies", "capture_level1_snapshot", "level1_capture", "run_level1_capture_service",
    "run_level1_snapshot_loop",
]
