"""Scheduled research-only capture of the opening-auction evidence pool."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from . import derived_daily_readings
from .auction_open_desk import VERSION, auction_timetable, auction_track_summary, build_auction_table
from .datasources.sources.tdx_protocol import TdxProtocolError
from .fuyao_provider import FuyaoProviderError
from .licensed_stock_api import UpstreamStockApiError
from .market_event_capture import normalize_fuyao_auction
from .market_session_repository import market_observation_session
from .public_market_repository import persist_timed_observations
from .public_provider_rate_limits import PublicProviderRateLimited
from .runtime_executors import ExecutorSaturatedError

AUCTION_PROVIDER = "fuyao_ths"
AUCTION_CAPABILITY = "auction_pool_snapshot"
CURVE_PROVIDER = "tdx_public"
CURVE_CAPABILITY = "auction_curve_daily"
BID_PROVIDER = "longhuvip"
BID_CAPABILITY = "auction_bid_daily"
TABLE_CAPABILITY = "auction_open_desk_daily"
CN_TZ = ZoneInfo("Asia/Shanghai")
DATABASE_UNAVAILABLE = (ExecutorSaturatedError, TimeoutError)
SESSION_GATE_TIMEOUT_SECONDS = 10
PERSIST_TIMEOUT_SECONDS = 60
FUYAO_REQUEST_TIMEOUT_SECONDS = 4


def _database_busy(error: BaseException) -> dict[str, Any]:
    return {"status": "database_busy", "error": str(error) or type(error).__name__}


def _all_final(events: list[dict[str, Any]], pool: list[str]) -> bool:
    final_symbols = {
        event["ts_code"] for event in events
        if str(event["raw"].get("data_status") or "") == "final"
    }
    return set(pool) <= final_symbols


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
    read_scored_rows: Callable[[Any, date], list[dict[str, Any]]] = lambda _database, _day: []
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)
    sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep
    session_gate: Callable[[Any, datetime], tuple[bool, str]] = market_observation_session


@dataclass
class AuctionPoolCaptureRuntime:
    deps: AuctionPoolCaptureDependencies

    async def _persist(self, provider: str, capability: str, row: dict[str, Any]) -> int:
        return await self.deps.run_database(
            persist_timed_observations, self.deps.database, provider, capability, [row],
            timeout_seconds=PERSIST_TIMEOUT_SECONDS,
        )

    async def _persist_table(self, reading: dict[str, Any]) -> dict[str, int]:
        return await self.deps.run_database(
            derived_daily_readings.store, self.deps.database, TABLE_CAPABILITY, [reading],
            timeout_seconds=PERSIST_TIMEOUT_SECONDS,
        )

    async def _capture_tick(self, scheduled_at: datetime, pool: list[str], finality_symbols: list[str] | None = None) -> dict[str, Any]:
        rows: list[dict[str, Any]] = []
        events: list[dict[str, Any]] = []
        status = "stored"
        for offset in range(0, len(pool), 100):
            symbols = pool[offset:offset + 100]
            try:
                payload = await asyncio.wait_for(self.deps.fetch_fuyao(
                    "a_share_auction_snapshot", {"thscodes": ",".join(symbols)}, slot_wait_seconds=2,
                ), timeout=FUYAO_REQUEST_TIMEOUT_SECONDS)
                normalized = normalize_fuyao_auction(payload, scheduled_at)
                events.extend(normalized)
                rows.extend(event["raw"] for event in normalized)
            except FuyaoProviderError as error:
                status = "rate_limited" if isinstance(error.__cause__, PublicProviderRateLimited) else "failed"
                return await self._store_tick(scheduled_at, pool, rows, status, str(error))
            except asyncio.TimeoutError as error:
                return await self._store_tick(scheduled_at, pool, rows, "failed", str(error) or "Fuyao request timed out")
        finality_pool = finality_symbols or pool
        if scheduled_at.astimezone(CN_TZ).time() >= time(9, 25) and _all_final(events, finality_pool):
            status = "final"
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
        return {"status": status, "stored": int(stored), "final": status == "final", "rows": len(rows), "rows_data": rows}

    async def _capture_curves(self, session_date: date, pool: list[str]) -> dict[str, int]:
        counts = {"stored": 0, "failed": 0}
        effective_at = datetime.combine(session_date, time(9, 26), tzinfo=CN_TZ).isoformat()
        next_call = self.deps.now().astimezone(CN_TZ)
        for symbol in pool:
            now = self.deps.now().astimezone(CN_TZ)
            if now < next_call:
                await self.deps.sleep((next_call - now).total_seconds())
            try:
                curve = await self.deps.fetch_curve(symbol=symbol)
                payload = getattr(curve, "rows", curve)
                await self._persist(CURVE_PROVIDER, CURVE_CAPABILITY, {
                    "observation_symbol": symbol, "trade_date": session_date.isoformat(), "curve": payload,
                    "research_only": True, "live_effect": "none", "effective_at": effective_at,
                    "available_at": self.deps.now().astimezone(timezone.utc).isoformat(),
                })
                counts["stored"] += 1
            except (TdxProtocolError, OSError, ValueError, ExecutorSaturatedError, TimeoutError):
                counts["failed"] += 1
            next_call = max(next_call, self.deps.now().astimezone(CN_TZ)) + timedelta(seconds=1)
        return counts

    async def _capture_bids(self, session_date: date, pool: list[str]) -> dict[str, int]:
        start = datetime.combine(session_date, time(9, 30), tzinfo=CN_TZ)
        now = self.deps.now().astimezone(CN_TZ)
        if now < start:
            await self.deps.sleep((start - now).total_seconds())
        counts = {"stored": 0, "rate_limited": 0, "failed": 0}
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
                counts["stored"] += 1
            except (UpstreamStockApiError, ExecutorSaturatedError, TimeoutError):
                counts["failed"] += 1
            next_call = max(next_call, self.deps.now().astimezone(CN_TZ)) + timedelta(seconds=1)
        return counts

    async def capture_session(self, session_date: date | None = None) -> dict[str, Any]:
        observed = self.deps.now().astimezone(CN_TZ)
        trade_date = session_date or observed.date()
        if observed.date() != trade_date or not (time(9, 14) <= observed.time() <= time(11, 30)):
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
        try:
            allowed, storage = await self.deps.storage_allowed()
        except DATABASE_UNAVAILABLE as error:
            return _database_busy(error)
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
        try:
            scored_rows = await self.deps.run_database(
                self.deps.read_scored_rows, self.deps.database, trade_date, timeout_seconds=SESSION_GATE_TIMEOUT_SECONDS,
            )
        except DATABASE_UNAVAILABLE as error:
            return _database_busy(error)
        summary = {"scheduled": 0, "stored": 0, "rate_limited": 0, "failed": 0, "curves": 0, "bids": 0}
        table_stored = False
        ran_tick = False
        ran_table_tick = False
        last_rows: list[dict[str, Any]] = []
        scored_pool = [row for row in scored_rows if str(row["symbol"]) in pool]
        for scheduled_at in auction_timetable(trade_date):
            now = self.deps.now().astimezone(CN_TZ)
            if now >= scheduled_at:
                continue
            await self.deps.sleep((scheduled_at - now).total_seconds())
            ran_tick = True
            ran_table_tick = ran_table_tick or scheduled_at.astimezone(CN_TZ).time() >= time(9, 25)
            summary["scheduled"] += 1
            result = await self._capture_tick(scheduled_at, pool, [str(row["symbol"]) for row in scored_pool])
            last_rows = result.get("rows_data") or []
            summary["stored"] += int(result.get("stored") or 0)
            if result.get("status") in {"rate_limited", "failed"}:
                summary[result["status"]] += 1
            if result.get("final"):
                if scored_pool:
                    reading = build_auction_table(scored_pool, last_rows, trade_date=trade_date)
                    await self._persist_table(reading)
                    table_stored = True
                break
        if scored_pool and ran_tick and ran_table_tick and not table_stored:
            await self._persist_table(build_auction_table(scored_pool, last_rows, trade_date=trade_date))
        summary["curves"] = await self._capture_curves(trade_date, pool)
        summary["bids"] = await self._capture_bids(trade_date, pool)
        return {"status": "completed", **summary, "table_stored": bool(scored_pool)}


async def run_auction_pool_capture_loop(
    runtime: AuctionPoolCaptureRuntime, *, log: Callable[[str], None] = print,
) -> None:
    while True:
        now = runtime.deps.now().astimezone(CN_TZ)
        if now.weekday() < 5 and time(9, 14) <= now.time() <= time(11, 30):
            result = await runtime.capture_session(now.date())
            while result.get("status") == "database_busy":
                retry_at = runtime.deps.now().astimezone(CN_TZ)
                if retry_at.time() > time(11, 30):
                    break
                await runtime.deps.sleep(5)
                result = await runtime.capture_session(retry_at.date())
            log("auction_pool_capture " + " ".join(f"{key}={result.get(key, 0)}" for key in (
                "scheduled", "stored", "rate_limited", "failed", "curves", "bids",
            )))
            after = runtime.deps.now().astimezone(CN_TZ)
            target = datetime.combine(after.date() + timedelta(days=1), time(9, 14), tzinfo=CN_TZ)
            sleep_from = after
        else:
            target = datetime.combine(now.date(), time(9, 14), tzinfo=CN_TZ)
            if now >= target:
                target += timedelta(days=1)
            sleep_from = now
        await runtime.deps.sleep(max(0, (target - sleep_from).total_seconds()))


def daily_series(database: Any, day: date | None = None) -> dict[str, Any]:
    from . import derived_daily_readings
    end = day or datetime.now(CN_TZ).date()
    with database.transaction() as connection:
        readings = derived_daily_readings.newest(connection, TABLE_CAPABILITY, end - timedelta(days=800), end)
    valid = [reading for reading in readings if reading.get("version") == VERSION]
    if day is not None:
        valid = [reading for reading in valid if reading.get("trade_date") == day.isoformat()]
    return {"version": VERSION, "reading": valid[-1] if valid else None,
            "stale_versions": len(readings) - len(valid), "research_only": True, "live_effect": "none"}


def track(database: Any, days: int = 120) -> dict[str, Any]:
    from . import derived_daily_readings
    with database.transaction() as connection:
        sessions = list(reversed(connection.execute(
            """SELECT calendar_date FROM quant.market_trade_calendar
                 WHERE exchange='SSE' AND is_open ORDER BY calendar_date DESC LIMIT %s""", (days,)).fetchall()))
        if not sessions:
            return {"version": VERSION, "days": [], "stale_versions": 0, "research_only": True, "live_effect": "none"}
        readings = derived_daily_readings.newest(connection, TABLE_CAPABILITY, sessions[0]["calendar_date"], sessions[-1]["calendar_date"])
        stale = len(readings) - sum(reading.get("version") == VERSION for reading in readings)
        readings = [reading for reading in readings if reading.get("version") == VERSION]
        symbols, dates = [], []
        for reading in readings:
            day = reading["trade_date"]
            for row in reading.get("rows") or []:
                symbols.append(str(row["symbol"]))
                dates.append(day)
        bars = connection.execute(
            """WITH wanted(symbol,trading_date) AS (SELECT * FROM unnest(%s::text[], %s::date[]))
               SELECT b.symbol,b.trading_date,b.open,b.close
                 FROM quant.canonical_bars_daily b JOIN wanted w
                   ON w.symbol=b.symbol AND w.trading_date=b.trading_date
                WHERE NOT coalesce(b.is_suspended, false) AND b.pre_close > 0 AND b.open > 0 AND b.close > 0""",
            (symbols, dates),
        ).fetchall() if symbols else []
    return {**auction_track_summary(readings, bars), "stale_versions": stale}


__all__ = [
    "AUCTION_CAPABILITY", "AUCTION_PROVIDER", "AuctionPoolCaptureDependencies", "AuctionPoolCaptureRuntime",
    "BID_CAPABILITY", "BID_PROVIDER", "CURVE_CAPABILITY", "CURVE_PROVIDER", "TABLE_CAPABILITY", "daily_series",
    "run_auction_pool_capture_loop", "track",
]
