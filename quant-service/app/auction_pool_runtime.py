"""Scheduled research-only capture of the opening-auction evidence pool."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from .auction_open_desk import auction_timetable
from .market_session_repository import market_observation_session
from .public_market_repository import persist_timed_observations
from .public_provider_rate_limits import PublicProviderRateLimited, acquire_public_provider_slot
from .runtime_executors import ExecutorSaturatedError

AUCTION_PROVIDER = "fuyao_ths"
AUCTION_CAPABILITY = "auction_pool_snapshot"
CURVE_PROVIDER = "tdx_public"
CURVE_CAPABILITY = "auction_curve_daily"
BID_PROVIDER = "longhuvip"
BID_CAPABILITY = "auction_bid_daily"
CN_TZ = ZoneInfo("Asia/Shanghai")
DATABASE_UNAVAILABLE = (ExecutorSaturatedError, TimeoutError)
SESSION_GATE_TIMEOUT_SECONDS = 10
PERSIST_TIMEOUT_SECONDS = 60


def _database_busy(error: BaseException) -> dict[str, Any]:
    return {"status": "database_busy", "error": str(error) or type(error).__name__}


def _payload_rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, Mapping):
        return [row for row in payload.get("item") or [] if isinstance(row, dict)]
    return [row for row in payload if isinstance(row, dict)] if isinstance(payload, list) else []


def _all_final(rows: list[dict[str, Any]], pool: list[str]) -> bool:
    return len(rows) >= len(pool) and all(str(row.get("data_status") or "") == "final" for row in rows)


def _stock_id(symbol: str) -> str:
    return symbol.split(".", 1)[0]


@dataclass(frozen=True)
class AuctionPoolCaptureDependencies:
    fetch_fuyao: Callable[..., Awaitable[Any]]
    fetch_curve: Callable[..., Awaitable[Any]]
    call_longhu: Callable[[dict[str, Any]], Awaitable[Any]]
    run_database: Callable[..., Awaitable[Any]]
    database: Any
    storage_allowed: Callable[[], Awaitable[tuple[bool, dict[str, Any]]]]
    read_pool: Callable[[Any, date], list[str]]
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)
    sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep
    acquire_tdx_slot: Callable[..., Awaitable[None]] = acquire_public_provider_slot
    session_gate: Callable[[Any, datetime], tuple[bool, str]] = market_observation_session


@dataclass
class AuctionPoolCaptureRuntime:
    deps: AuctionPoolCaptureDependencies

    async def _persist(self, provider: str, capability: str, row: dict[str, Any]) -> int:
        return await self.deps.run_database(
            persist_timed_observations, self.deps.database, provider, capability, [row],
            timeout_seconds=PERSIST_TIMEOUT_SECONDS,
        )

    async def _capture_tick(self, scheduled_at: datetime, pool: list[str]) -> dict[str, Any]:
        rows: list[dict[str, Any]] = []
        status = "stored"
        for offset in range(0, len(pool), 100):
            symbols = pool[offset:offset + 100]
            try:
                payload = await self.deps.fetch_fuyao(
                    "a_share_auction_snapshot", {"thscodes": ",".join(symbols)}, slot_wait_seconds=2,
                )
            except PublicProviderRateLimited as error:
                status = "rate_limited"
                return await self._store_tick(scheduled_at, pool, rows, status, str(error))
            except Exception as error:  # one batch must not stop the timetable
                status = "rate_limited" if "rate_limited" in str(error) else "failed"
                return await self._store_tick(scheduled_at, pool, rows, status, str(error))
            rows.extend(_payload_rows(payload))
        status = "final" if _all_final(rows, pool) else status
        return await self._store_tick(scheduled_at, pool, rows, status, None)

    async def _store_tick(
        self, scheduled_at: datetime, pool: list[str], rows: list[dict[str, Any]], status: str, error: str | None,
    ) -> dict[str, Any]:
        document: dict[str, Any] = {
            "scheduled_at": scheduled_at.isoformat(), "pool_size": len(pool), "rows": rows,
            "status": status, "research_only": True, "live_effect": "none",
            "effective_at": scheduled_at.isoformat(), "available_at": self.deps.now().astimezone(timezone.utc).isoformat(),
        }
        if error:
            document["error"] = error[:300]
        try:
            stored = await self._persist(AUCTION_PROVIDER, AUCTION_CAPABILITY, document)
        except DATABASE_UNAVAILABLE as caught:
            return _database_busy(caught)
        return {"status": status, "stored": int(stored), "final": status == "final", "rows": len(rows)}

    async def _capture_curves(self, session_date: date, pool: list[str]) -> int:
        stored = 0
        effective_at = datetime.combine(session_date, time(9, 26), tzinfo=CN_TZ).isoformat()
        for symbol in pool:
            try:
                await self.deps.acquire_tdx_slot(CURVE_PROVIDER, max_wait_seconds=0)
                curve = await self.deps.fetch_curve(symbol=symbol)
                payload = getattr(curve, "rows", curve)
                await self._persist(CURVE_PROVIDER, CURVE_CAPABILITY, {
                    "observation_symbol": symbol, "trade_date": session_date.isoformat(), "curve": payload,
                    "research_only": True, "live_effect": "none", "effective_at": effective_at,
                    "available_at": self.deps.now().astimezone(timezone.utc).isoformat(),
                })
                stored += 1
            except Exception:
                continue
        return stored

    async def _capture_bids(self, session_date: date, pool: list[str]) -> int:
        start = datetime.combine(session_date, time(9, 30), tzinfo=CN_TZ)
        now = self.deps.now().astimezone(CN_TZ)
        if now < start:
            await self.deps.sleep((start - now).total_seconds())
        stored = 0
        effective_at = start.isoformat()
        next_call = start
        for symbol in pool:
            now = self.deps.now().astimezone(CN_TZ)
            if now < next_call:
                await self.deps.sleep((next_call - now).total_seconds())
            request = {"target": "longhu_quote", "path": "/w1/api/index.php", "params": {
                "a": "GetStockBid", "c": "StockL2Data", "PhoneOSNew": 1, "VerSion": "5.20.0.2",
                "apiv": "w41", "StockID": _stock_id(symbol),
            }}
            try:
                bid = await self.deps.call_longhu(request)
                await self._persist(BID_PROVIDER, BID_CAPABILITY, {
                    "observation_symbol": symbol, "trade_date": session_date.isoformat(), "bid": bid,
                    "research_only": True, "live_effect": "none", "effective_at": effective_at,
                    "available_at": self.deps.now().astimezone(timezone.utc).isoformat(),
                })
                stored += 1
            except Exception:
                pass
            next_call = max(next_call, self.deps.now().astimezone(CN_TZ)) + timedelta(seconds=1)
        return stored

    async def capture_session(self, session_date: date | None = None) -> dict[str, Any]:
        observed = self.deps.now().astimezone(CN_TZ)
        trade_date = session_date or observed.date()
        if observed.date() != trade_date or not (time(9, 14) <= observed.time() <= time(9, 30)):
            return {"status": "outside_session"}
        first_tick = auction_timetable(trade_date)[0]
        try:
            active, _reason = await self.deps.run_database(
                self.deps.session_gate, self.deps.database, first_tick, timeout_seconds=SESSION_GATE_TIMEOUT_SECONDS,
            )
        except DATABASE_UNAVAILABLE as error:
            return _database_busy(error)
        if not active:
            return {"status": "outside_session"}
        allowed, storage = await self.deps.storage_allowed()
        if not allowed:
            return {"status": "storage_paused", "storage": storage}
        try:
            pool = await self.deps.run_database(
                self.deps.read_pool, self.deps.database, trade_date, timeout_seconds=SESSION_GATE_TIMEOUT_SECONDS,
            )
        except DATABASE_UNAVAILABLE as error:
            return _database_busy(error)
        if not pool:
            return {"status": "no_pool"}
        summary = {"scheduled": 0, "stored": 0, "rate_limited": 0, "failed": 0, "curves": 0, "bids": 0}
        for scheduled_at in auction_timetable(trade_date):
            now = self.deps.now().astimezone(CN_TZ)
            if now >= scheduled_at:
                continue
            await self.deps.sleep((scheduled_at - now).total_seconds())
            summary["scheduled"] += 1
            result = await self._capture_tick(scheduled_at, pool)
            summary["stored"] += int(result.get("stored") or 0)
            if result.get("status") in {"rate_limited", "failed"}:
                summary[result["status"]] += 1
            if result.get("final"):
                break
        summary["curves"] = await self._capture_curves(trade_date, pool)
        summary["bids"] = await self._capture_bids(trade_date, pool)
        return {"status": "completed", **summary}


async def run_auction_pool_capture_loop(
    runtime: AuctionPoolCaptureRuntime, *, log: Callable[[str], None] = print,
) -> None:
    while True:
        now = runtime.deps.now().astimezone(CN_TZ)
        if now.weekday() < 5 and time(9, 14) <= now.time() <= time(9, 30):
            result = await runtime.capture_session(now.date())
            log("auction_pool_capture " + " ".join(f"{key}={result.get(key, 0)}" for key in (
                "scheduled", "stored", "rate_limited", "failed", "curves", "bids",
            )))
            target = datetime.combine(now.date() + timedelta(days=1), time(9, 14), tzinfo=CN_TZ)
        else:
            target = datetime.combine(now.date(), time(9, 14), tzinfo=CN_TZ)
            if now >= target:
                target += timedelta(days=1)
        await runtime.deps.sleep(max(0, (target - now).total_seconds()))


__all__ = [
    "AUCTION_CAPABILITY", "AUCTION_PROVIDER", "AuctionPoolCaptureDependencies", "AuctionPoolCaptureRuntime",
    "BID_CAPABILITY", "BID_PROVIDER", "CURVE_CAPABILITY", "CURVE_PROVIDER", "run_auction_pool_capture_loop",
]
