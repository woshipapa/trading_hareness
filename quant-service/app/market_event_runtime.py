"""Cadence-only runner for all-A auction/limit-pool evidence capture."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, time, timezone
from typing import Any
from zoneinfo import ZoneInfo

CN_TZ = ZoneInfo("Asia/Shanghai")


def event_capture_window(now: datetime) -> tuple[bool, bool]:
    local = now.astimezone(CN_TZ)
    if local.weekday() >= 5:
        return False, False
    current = local.time()
    active = (time(9, 15) <= current <= time(11, 30)) or (time(13, 0) <= current <= time(15, 0))
    return active, current >= time(14, 57)


async def run_market_event_capture_loop(
    *,
    interval_seconds: int,
    capture: Callable[..., Awaitable[dict[str, Any]]],
    capture_longhu_auction: Callable[[datetime], Awaitable[dict[str, Any]]] | None = None,
    session_open: Callable[[datetime], Awaitable[bool]],
    symbols: Callable[[], Awaitable[Sequence[str]]],
    log: Callable[[str], None] = print,
) -> None:
    last_auction_date: str | None = None
    last_longhu_auction_date: str | None = None
    while True:
        now = datetime.now(timezone.utc)
        active, auction_window = event_capture_window(now)
        if active and await session_open(now):
            local_date = now.astimezone(CN_TZ).date().isoformat()
            include_auction = auction_window and last_auction_date != local_date
            try:
                result = await capture(
                    now, include_auction=include_auction,
                    auction_symbols=await symbols() if include_auction else (),
                )
                if include_auction and result.get("auction", {}).get("received", 0) > 0:
                    last_auction_date = local_date
                if capture_longhu_auction and time(9, 25) <= now.astimezone(CN_TZ).time() <= time(9, 30) and last_longhu_auction_date != local_date:
                    longhu_result = await capture_longhu_auction(now)
                    if longhu_result.get("status") == "completed":
                        last_longhu_auction_date = local_date
                    elif longhu_result.get("status") not in {"completed", "skipped"}:
                        log(f"Longhu morning-auction evidence degraded: {str(longhu_result)[:500]}")
                if result.get("status") not in {"completed", "empty"}:
                    log(f"market event evidence capture degraded: {str(result)[:500]}")
            except Exception as error:  # noqa: BLE001 - next cadence retries
                log(f"market event evidence capture failed: {str(error)[:300]}")
        await asyncio.sleep(max(10, min(300, int(interval_seconds))))


#: Fuyao capability -> the data-source catalog capability its health is recorded under.
HEALTH_NAMES = {
    "a_share_limit_up_pool": "limits.limit_up_pool",
    "a_share_limit_break_pool": "limits.broken_pool",
    "a_share_limit_down_pool": "limits.limit_down_pool",
    "a_share_limit_up_ladder": "limits.ladder",
    "a_share_auction_short_term_benchmark": "auction.short_term_benchmark",
    "a_share_auction_snapshot": "auction.open_snapshot",
    "a_share_hot_stock_list": "attention.ths_hot_rank",
    "a_share_skyrocket_list": "attention.ths_skyrocket",
    "a_share_anomaly_analysis_list": "limits.anomaly_tape",
}


@dataclass(frozen=True)
class MarketEventCaptureDependencies:
    """What the 60-second Fuyao/Longhu evidence capture needs from the rest of the service."""

    fetch_fuyao: Callable[[str, dict[str, Any]], Awaitable[Mapping[str, Any]]]
    run_database: Callable[..., Awaitable[Any]]
    database: Any
    persist_market_events: Callable[..., int]
    persist_timed_observations: Callable[..., int]
    session_open: Callable[[datetime], Awaitable[tuple[bool, str]]]
    all_a_snapshot: Callable[[], Awaitable[tuple[list[dict[str, Any]], Any]]]
    universe_symbols: Callable[[], list[str]]
    health_capability: Callable[..., str]
    record_success: Callable[..., Any]
    record_failure: Callable[..., Any]
    longhu_configured: Callable[[], bool]
    vendor_call: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]
    capture_events: Callable[..., Awaitable[dict[str, Any]]]
    capture_longhu_auction: Callable[..., Awaitable[dict[str, Any]]]


async def run_market_event_capture_service(deps: MarketEventCaptureDependencies) -> None:
    """Persist Fuyao all-A auction/pool/chain evidence on a 60s cadence."""
    async def persist(provider: str, rows: list[dict[str, Any]]) -> int:
        return await deps.run_database(deps.persist_market_events, provider, rows, timeout_seconds=60)

    async def open_session(now: datetime) -> bool:
        active, _reason = await deps.session_open(now)
        return active

    async def all_symbols() -> Sequence[str]:
        # Fuyao rejects a whole 100-code batch for one delisted or index code,
        # so its own live code list is the auction universe; the local
        # universe is only the fallback.
        try:
            rows, _meta = await deps.all_a_snapshot()
            if rows:
                return [str(row["symbol"]) for row in rows]
        except Exception as error:  # noqa: BLE001 - fall back to the local universe
            print(f"Fuyao code list unavailable for the auction capture: {str(error)[:200]}")
        return await deps.run_database(deps.universe_symbols, timeout_seconds=15)

    async def persist_observations(provider: str, capability: str, rows: list[dict[str, Any]]) -> int:
        return await deps.run_database(deps.persist_timed_observations, provider, capability, rows, timeout_seconds=60)

    async def persist_health(provider: str, capability: str, rows: int, error: str | None) -> None:
        health_capability_name = deps.health_capability(
            provider, HEALTH_NAMES.get(capability, capability), fallback=capability,
        )

        def write() -> None:
            with deps.database.transaction() as connection:
                if error is None and rows > 0:
                    deps.record_success(connection, provider, health_capability_name, rows, None)
                else:
                    deps.record_failure(
                        connection, provider, health_capability_name,
                        error or f"empty response for {capability}", None,
                    )

        await deps.run_database(write, timeout_seconds=10)

    async def longhu_auction(observed_at: datetime) -> dict[str, Any]:
        if not deps.longhu_configured():
            return {"status": "skipped", "reason": "longhu_not_configured", "stored": 0}
        return await deps.capture_longhu_auction(observed_at, call=deps.vendor_call, persist=persist)

    attention_state: dict[str, Any] = {}
    await run_market_event_capture_loop(
        interval_seconds=60, capture=lambda observed_at, **kwargs: deps.capture_events(
            observed_at, fetch=deps.fetch_fuyao, persist=persist, persist_observations=persist_observations,
            persist_health=persist_health, state=attention_state, **kwargs,
        ), capture_longhu_auction=longhu_auction, session_open=open_session, symbols=all_symbols,
    )


__all__ = [
    "HEALTH_NAMES", "MarketEventCaptureDependencies", "event_capture_window", "run_market_event_capture_loop",
    "run_market_event_capture_service",
]
