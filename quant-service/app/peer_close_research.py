"""The peer's own post-close research stages, on the peer's own schedule.

The full post-close refresh (``post_close_refresh_service``) is the owner's:
its 15681 runs its own pipeline at every close, while the peer's copy is only
reachable by hand.  These stages belong to the peer alone - the teacher plan
roll, its outcome review, the watch-list daily review and the 小杰 settlement -
so they never ran on their own: after 2026-09-22's close the 09-21 teacher
pack's plans were neither settled nor carried into 09-23.

This runs just those, once per trading date, after the day's bars have
landed, each behind the same durable receipt the orchestrator would use (a
completed stage is never repeated).  Before 09:00 it catches up the previous
session, so a restart overnight does not skip a day; between 09:00 and the
close-ready time it does nothing.

Since decision 0021 it also starts the daily products the peer added to the
stage table after the owner's copy was cut (2026-09-20): the limit-up lists,
the limit-up risk reading, the sentiment temperature, the broad index-fund flow
and the market timing.  Adding a stage to the table alone schedules nothing on
the peer.  Their receipts carry the ``peer_`` prefix, so a receipt the stage
table closed for a skipped or empty run can never stand in for the product.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from .limit_up_risk_repository import live_lists_stored
from .limit_up_risk_runtime import refresh as refresh_limit_up_risk
from .market_history_backfill import (
    SOURCES, append_limit_performance, backfill_allowed, longhu_limit_performance, walk_source,
)
from .market_session_rules import exchange_date
from .market_temperature_runtime import refresh as refresh_market_temperature

#: Order matters: the outcome review reads the settlement the roll just wrote,
#: and the digest reads what all of them archived.
#: The teacher stages keep their place; the daily products follow, so none of them can delay the first five.
STAGES = ("teacher_review_roll", "teacher_outcome_review", "watch_daily_review", "xiaojie_outcomes",
          "daily_digest",
          "peer_limit_performance_lists", "peer_limit_up_risk", "peer_market_temperature",
          "peer_broad_etf_flow", "peer_market_timing")
STAGE_TIMEOUT_SECONDS = {"teacher_review_roll": 300.0, "teacher_outcome_review": 240.0,
                         "watch_daily_review": 240.0, "xiaojie_outcomes": 150.0, "daily_digest": 120.0,
                         "peer_limit_performance_lists": 120.0, "peer_limit_up_risk": 120.0,
                         "peer_market_temperature": 180.0, "peer_broad_etf_flow": 180.0,
                         "peer_market_timing": 120.0}
#: A stage whose named stage did not complete for the same day is recorded blocked and tried on the next tick.
STAGE_DEPENDENCIES = {"peer_limit_up_risk": "peer_limit_performance_lists"}
#: What the loop also runs for the session before the target: an evening that failed is repaired the next day.
REPAIR_STAGES = ("peer_limit_performance_lists", "peer_limit_up_risk")
#: The owner's close pipeline has published the day's bars by ~16:05.
CLOSE_READY_AT = time(16, 15)
SESSION_GUARD_FROM = time(9, 0)
#: A session's full-market bars count as landed above this many symbols.
MIN_DAILY_BARS = 4000


async def target_session(now_local: datetime, calendar_open: Callable[[date], Awaitable[bool]]) -> date | None:
    """The session whose close research is due now, if any."""
    today = now_local.date()
    if now_local.time() >= CLOSE_READY_AT:
        return today if await calendar_open(today) else None
    if now_local.time() >= SESSION_GUARD_FROM:
        return None
    for back in range(1, 12):
        day = today - timedelta(days=back)
        if await calendar_open(day):
            return day
    return None


def daily_bars_ready(database: Any, trade_date: date) -> bool:
    with database.transaction() as connection:
        row = connection.execute(
            "SELECT count(*)::int AS n FROM quant.canonical_bars_daily WHERE trading_date=%s", (trade_date,),
        ).fetchone()
    return int((row or {}).get("n") or 0) >= MIN_DAILY_BARS


async def run_due(trade_date: date, *, stages: Mapping[str, Callable[[date], Awaitable[Any]]],
                  record: Callable[[str, date, Callable[[], Any]], Awaitable[Any]],
                  names: tuple[str, ...] = STAGES) -> dict[str, Any]:
    """Run each stage behind its receipt; one failure never stops the next."""
    results: dict[str, Any] = {}
    reasons: dict[str, Any] = {}
    for name in names:
        action = stages.get(name)
        if action is None:
            continue
        dependency = STAGE_DEPENDENCIES.get(name)
        if dependency is not None and results.get(dependency) != "completed":
            async def action(_day: date, dependency: str = dependency) -> dict[str, Any]:
                return {"status": "blocked", "reason": f"waiting for {dependency}"}

        async def bounded(name: str = name, action: Callable[[date], Awaitable[Any]] = action) -> Any:
            return await asyncio.wait_for(action(trade_date), timeout=STAGE_TIMEOUT_SECONDS.get(name, 120.0))

        try:
            result = await record(name, trade_date, bounded)
            results[name] = (result or {}).get("status", "completed") if isinstance(result, dict) else "completed"
            if isinstance(result, dict) and result.get("reason"):
                reasons[name] = result["reason"]
        except Exception as error:  # noqa: BLE001 - the receipt records the failure; later stages still run
            results[name] = f"failed: {str(error)[:160]}"
    return {"trade_date": trade_date.isoformat(), "stages": results, "reasons": reasons}


def open_stages(result: Mapping[str, Any]) -> str:
    """The stages a tick left open, with their reason: the line the loop prints."""
    left = [f"{name}={status}" + (f" ({result['reasons'][name]})" if name in result["reasons"] else "")
            for name, status in result["stages"].items()
            if status in ("partial", "blocked") or status.startswith("failed")]
    return f"peer close research {result['trade_date']} left open: " + "; ".join(left) if left else ""


SHANGHAI = ZoneInfo("Asia/Shanghai")
TEACHER_DISABLED = {"status": "skipped", "reason": "teacher review disabled", "research_only": True}


@dataclass(frozen=True)
class PeerCloseDependencies:
    """What the peer's close loop needs from the rest of the service."""

    database: Any
    run_database: Callable[..., Awaitable[Any]]
    calendar_open: Callable[[date], Awaitable[bool]]
    teacher_enabled: Callable[[], bool]
    teacher_roll: Callable[[date], Awaitable[dict[str, Any]]]
    teacher_outcome: Callable[[date], Awaitable[dict[str, Any]]]
    watch_review: Callable[[date], Awaitable[Any]]
    settle_xiaojie: Callable[[date], Any]
    daily_digest: Callable[[date], Awaitable[Any]]
    record_stage: Callable[..., Awaitable[Any]]
    safe_error: Callable[[str, int], str]
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)
    sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep
    #: The licensed read call. With it the loop also runs the daily products (lists, risk, temperature).
    call_longhu: Callable[[dict[str, Any]], Awaitable[Any]] | None = None
    refresh_broad_etf_flow: Callable[[date], Awaitable[Any]] | None = None
    refresh_market_timing: Callable[[date], Awaitable[Any]] | None = None


def peer_stages(deps: PeerCloseDependencies) -> dict[str, Callable[[date], Awaitable[Any]]]:
    async def teacher_roll(trade_date: date) -> dict[str, Any]:
        return dict(TEACHER_DISABLED) if not deps.teacher_enabled() else await deps.teacher_roll(trade_date)

    async def teacher_outcome(trade_date: date) -> dict[str, Any]:
        return dict(TEACHER_DISABLED) if not deps.teacher_enabled() else await deps.teacher_outcome(trade_date)

    stages: dict[str, Callable[[date], Awaitable[Any]]] = {
        "teacher_review_roll": teacher_roll,
        "teacher_outcome_review": teacher_outcome,
        "watch_daily_review": lambda trade_date: deps.watch_review(trade_date),
        "xiaojie_outcomes": lambda trade_date: deps.run_database(deps.settle_xiaojie, trade_date, timeout_seconds=110),
        "daily_digest": lambda trade_date: deps.daily_digest(trade_date),
    }
    if deps.call_longhu is not None:
        stages.update(_product_stages(deps))
    if deps.refresh_broad_etf_flow is not None:
        stages["peer_broad_etf_flow"] = deps.refresh_broad_etf_flow
    if deps.refresh_market_timing is not None:
        stages["peer_market_timing"] = deps.refresh_market_timing
    return stages


def _live_lists_stored(database: Any, trade_date: date) -> bool:
    with database.transaction() as connection:
        return live_lists_stored(connection, trade_date)


def _product_stages(deps: PeerCloseDependencies) -> dict[str, Callable[[date], Awaitable[Any]]]:
    """The receipts of these close only when the day's product is stored."""
    async def history_lists(trade_date: date) -> dict[str, Any]:
        async def allowed() -> bool:
            now = deps.now()
            return backfill_allowed(now, await deps.calendar_open(now.astimezone(SHANGHAI).date()))

        report = await walk_source(
            "longhu_limit_performance", [trade_date], longhu_limit_performance(deps.call_longhu),
            database=deps.database, run_database=deps.run_database, allowed=allowed,
            pace_seconds=SOURCES["longhu_limit_performance"]["pace"], log=lambda _text: None)
        if report["stopped"]:
            return {"status": "blocked", "reason": report["stopped"]}
        if report["errors"]:
            return {"status": "partial", "reason": f"history lists failed: {report['errors']}"}
        if report["stored"] or report["skipped"]:
            return {"status": "completed", "source": "history", "stored": report["stored"]}
        return {"status": "partial", "reason": "the history source does not serve the day yet"}

    async def limit_lists(trade_date: date) -> dict[str, Any]:
        if await deps.run_database(_live_lists_stored, deps.database, trade_date, timeout_seconds=30):
            return {"status": "completed", "source": "live", "stored": 0}
        if trade_date != exchange_date(deps.now()):
            # The live lists exist on the day only; history is asked for a day that has none.
            return await history_lists(trade_date)
        result = await append_limit_performance(
            trade_date, deps.call_longhu, database=deps.database, run_database=deps.run_database, now=deps.now())
        if result["status"] in ("completed", "unchanged"):
            return {**result, "status": "completed"}
        return {**result, "status": "partial", "reason": result.get("reason") or f"live lists {result['status']}"}

    async def limit_up_risk(trade_date: date) -> dict[str, Any]:
        result = await deps.run_database(refresh_limit_up_risk, deps.database, trade_date, timeout_seconds=120)
        if result["status"] == "no_lists":
            return {**result, "status": "blocked", "reason": "no limit lists stored for the day"}
        return result

    return {
        "peer_limit_performance_lists": limit_lists,
        "peer_limit_up_risk": limit_up_risk,
        "peer_market_temperature": lambda trade_date: deps.run_database(
            refresh_market_temperature, deps.database, trade_date, timeout_seconds=180),
    }


async def previous_session(trade_date: date, calendar_open: Callable[[date], Awaitable[bool]]) -> date | None:
    """The open session before ``trade_date``."""
    for back in range(1, 12):
        day = trade_date - timedelta(days=back)
        if await calendar_open(day):
            return day
    return None


async def run_peer_close_loop(deps: PeerCloseDependencies) -> None:
    """Every ten minutes: run whatever close stage is due for the target session."""
    stages = peer_stages(deps)

    async def record(name: str, trade_date: date, action: Callable[[], Any]) -> Any:
        return await deps.record_stage(name, trade_date, action, db=deps.database,
                                       run_database_blocking=deps.run_database, safe_error_detail=deps.safe_error)

    while True:
        try:
            trade_date = await target_session(deps.now().astimezone(SHANGHAI), deps.calendar_open)
            if trade_date is not None and await deps.run_database(
                    lambda: daily_bars_ready(deps.database, trade_date), timeout_seconds=30):
                results = [await run_due(trade_date, stages=stages, record=record)]
                # After today's stages, so the repair can never delay them.
                repair_day = await previous_session(trade_date, deps.calendar_open)
                if repair_day is not None:
                    results.append(await run_due(repair_day, stages=stages, record=record, names=REPAIR_STAGES))
                for line in filter(None, map(open_stages, results)):
                    print(line)
        except Exception as error:  # noqa: BLE001 - the next tick retries
            print(f"peer close research failed: {deps.safe_error(str(error), 300)}")
        await deps.sleep(600)


__all__ = [
    "CLOSE_READY_AT", "MIN_DAILY_BARS", "PeerCloseDependencies", "REPAIR_STAGES", "STAGES", "STAGE_DEPENDENCIES",
    "daily_bars_ready", "open_stages", "peer_stages", "previous_session", "run_due", "run_peer_close_loop",
    "target_session",
]
