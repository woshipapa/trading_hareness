"""Supervised two-second opening-auction observation loop."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .auction_pulse import PULSE_REQUESTS, alert_decision, auction_pulse_window, format_alert, summarize_pulse


async def run_auction_pulse_loop(
    *, interval_seconds: float, capture: Callable[[datetime], Awaitable[dict[str, Any]]],
    session_open: Callable[[datetime], Awaitable[bool]], log: Callable[[str], None] = print,
) -> None:
    """Capture every cadence tick in the 09:15-09:30 Shanghai window.

    The callback owns provider rate limiting, persistence and alert cooldown.
    This runner only schedules bounded observations and never catches a
    cancellation as a normal failure.
    """
    while True:
        now = datetime.now(timezone.utc)
        if auction_pulse_window(now):
            try:
                if await session_open(now):
                    result = await capture(now)
                    if result.get("status") not in {"completed", "partial", "skipped"}:
                        log(f"auction pulse degraded: {str(result)[:300]}")
            except asyncio.CancelledError:
                raise
            except Exception as error:  # noqa: BLE001 - next two-second tick retries
                log(f"auction pulse failed: {type(error).__name__}: {str(error)[:300]}")
        await asyncio.sleep(max(1.0, min(30.0, float(interval_seconds))))


@dataclass(frozen=True)
class AuctionPulseDependencies:
    """What one auction-pulse capture needs from the rest of the service."""

    vendor_configured: Callable[[], bool]
    call_vendor: Callable[[dict[str, Any]], Awaitable[Any]]
    persist: Callable[[list[dict[str, Any]]], Awaitable[int]]
    post_alert: Callable[[str], Awaitable[dict[str, Any]]]
    cooldown_seconds: Callable[[], int]
    session_open: Callable[[datetime], Awaitable[tuple[bool, str]]]


async def capture_auction_pulse(observed_at: datetime, state: dict[str, Any],
                                deps: AuctionPulseDependencies) -> dict[str, Any]:
    """Read Longhu's bounded opening-auction endpoints and emit cooled evidence."""
    if not deps.vendor_configured():
        return {"status": "skipped", "reason": "longhu_not_configured", "stored": 0}
    envelopes: dict[str, Any] = {}

    async def call_one(spec: dict[str, Any]) -> tuple[str, dict[str, Any] | None, str | None]:
        request = {"target": spec["target"], "params": dict(spec["params"])}
        try:
            result = await deps.call_vendor(request)
            pages = result.get("pages") if isinstance(result, Mapping) else []
            payloads = [page.get("payload") for page in pages if isinstance(page, Mapping) and isinstance(page.get("payload"), Mapping)]
            return str(spec["name"]), {"action": spec["action"], "payload": {"pages": payloads}}, None
        except Exception as error:  # noqa: BLE001 - one source must not hide the others
            return str(spec["name"]), None, f"{type(error).__name__}: {str(error)[:180]}"

    results = await asyncio.gather(*(call_one(spec) for spec in PULSE_REQUESTS))
    source_status: dict[str, Any] = {}
    for name, envelope, error in results:
        if envelope is not None:
            envelopes[name] = envelope
            pages = envelope["payload"].get("pages") or []
            source_status[name] = {"status": "completed", "action": envelope["action"], "pages": len(pages)}
        else:
            source_status[name] = {"status": "failed", "error": error}
    summary = summarize_pulse(envelopes, observed_at)
    summary["source_status"] = source_status
    stored = await deps.persist([{
        "effective_at": observed_at.isoformat(), "available_at": observed_at.isoformat(),
        "ts_code": None, "exchange_window": "opening_call_auction", "summary": summary,
        "source_status": source_status, "research_only": True, "live_effect": "none",
    }])
    decision = alert_decision(
        summary, state.get("last_summary"), now=observed_at,
        last_alert_at=state.get("last_alert_at"), cooldown_seconds=deps.cooldown_seconds(),
    )
    delivery: dict[str, Any] = {"status": "suppressed", "reason": decision["reason"]}
    if decision["should_send"]:
        delivery = await deps.post_alert(format_alert(summary))
        if delivery.get("status") == "sent":
            state["last_alert_at"] = observed_at
    state["last_summary"] = summary
    return {
        "status": "completed" if envelopes else "partial", "stored": stored,
        "sources": source_status, "alert": {**decision, "delivery": delivery},
        "research_only": True, "live_effect": "none",
    }


async def run_auction_pulse_service(deps: AuctionPulseDependencies, *, interval_seconds: float) -> None:
    """The supervised loop with one capture state for the process lifetime."""
    state: dict[str, Any] = {"last_summary": None, "last_alert_at": None}

    async def session_open(now: datetime) -> bool:
        active, _reason = await deps.session_open(now)
        return active

    await run_auction_pulse_loop(
        interval_seconds=interval_seconds,
        capture=lambda observed_at: capture_auction_pulse(observed_at, state, deps),
        session_open=session_open,
    )


__all__ = ["AuctionPulseDependencies", "capture_auction_pulse", "run_auction_pulse_loop", "run_auction_pulse_service"]
