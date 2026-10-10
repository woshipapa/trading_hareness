"""Wiring for the data-source collectors, shared by both deployment shapes.

* In process: the quant service starts ``public_evidence_capture`` and
  ``post_close_public_archive`` as leased background loops (default).
* Standalone: ``python -m app.datasources collect`` runs the same loops in a
  separate container.  It takes the *same* durable lease keys, so whichever
  process holds a lease is the only writer; the other waits.

This module needs a ``Database`` and optional Fuyao callables; it never
imports the service composition root or any strategy code.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import date
from typing import Any

from ..provider_health import record_provider_failure, record_provider_success
from ..public_market_repository import (
    latest_observation_payloads, observation_payloads, persist_market_events, persist_tdx_membership_delta,
    persist_timed_observations,
)
from ..daily_valuation_repository import project_valuations
from ..runtime_leases import (
    acquire_runtime_lease, background_loop_lease_seconds, release_runtime_lease, renew_runtime_lease,
)
from . import storage
from .bindings import register_package_sources
from .collectors import intraday, post_close
from .resolver import CapabilityResolver


COLLECTOR_TASKS: tuple[str, ...] = ("public_evidence_capture", "post_close_public_archive")
RunBlocking = Callable[..., Awaitable[Any]]


async def _to_thread(action: Callable[..., Any], *args: Any, timeout_seconds: float = 30) -> Any:
    return await asyncio.wait_for(asyncio.to_thread(action, *args), timeout=timeout_seconds)


def env_flags(prefix: str, keys: Sequence[str], environ: Mapping[str, str] | None = None,
              opt_in_keys: set[str] = frozenset()) -> dict[str, bool]:
    """Read per-key switches, with selected keys defaulting off."""
    values = os.environ if environ is None else environ
    return {key: str(values.get(f"{prefix}_{key.upper()}_ENABLED", "false" if key in opt_in_keys else "true")).strip().lower()
            in {"1", "true", "yes", "on"}
            for key in keys}


def build_collector_deps(
    database: Any,
    *,
    run_blocking: RunBlocking | None = None,
    fuyao_fetch: Callable[[str, dict[str, Any]], Awaitable[Mapping[str, Any]]] | None = None,
    fuyao_snapshot: Callable[[], Awaitable[tuple[list[dict[str, Any]], dict[str, Any]]]] | None = None,
    log: Callable[[str], None] = print,
) -> intraday.CollectorDeps:
    run = run_blocking or _to_thread

    async def persist_events(provider: str, rows: list[dict[str, Any]]) -> int:
        return await run(persist_market_events, database, provider, rows, timeout_seconds=60)

    async def persist_observations(provider: str, capability: str, rows: list[dict[str, Any]]) -> int:
        return await run(persist_timed_observations, database, provider, capability, rows, timeout_seconds=90)

    def write_health(provider: str, capability: str, ok: bool, rows: int, latency_ms: int | None, error: str | None) -> None:
        with database.transaction() as connection:
            if ok:
                record_provider_success(connection, provider, capability, rows, latency_ms)
            else:
                record_provider_failure(connection, provider, capability, error or "failed", latency_ms)

    async def record_health(provider: str, capability: str, ok: bool, rows: int,
                            latency_ms: int | None, error: str | None) -> None:
        await run(write_health, provider, capability, ok, rows, latency_ms, error, timeout_seconds=15)

    async def previous_turnover(day: date) -> float | None:
        return await run(storage.previous_close_turnover, database, day, timeout_seconds=15)

    async def concept_membership() -> dict[str, set[str]] | None:
        return await run(storage.fuyao_concept_membership, database, timeout_seconds=30)

    resolver = register_package_sources(CapabilityResolver(), fuyao_fetch=fuyao_fetch)

    return intraday.CollectorDeps(
        persist_events=persist_events, persist_observations=persist_observations, record_health=record_health,
        resolver=resolver,
        fuyao_fetch=fuyao_fetch, fuyao_snapshot=fuyao_snapshot, previous_turnover_total=previous_turnover,
        concept_membership=concept_membership, log=log,
    )


def build_archive_deps(database: Any, collector: intraday.CollectorDeps, *, run_blocking: RunBlocking | None = None,
                       environ: Mapping[str, str] | None = None,
                       persist_membership_delta: Callable[..., Awaitable[dict[str, int]]] | None = None) -> post_close.ArchiveDeps:
    run = run_blocking or _to_thread
    values = os.environ if environ is None else environ

    async def watch_symbols() -> Sequence[str]:
        return await run(storage.enabled_watch_symbols, database, 100, timeout_seconds=15)

    async def previous_day(day: date) -> date | None:
        return await run(storage.previous_trading_day, database, day, timeout_seconds=15)

    async def latest(provider: str, capability: str) -> dict[str, dict[str, Any]]:
        return await run(latest_observation_payloads, database, provider, capability, timeout_seconds=30)

    async def all_payloads(provider: str, capability: str) -> list[dict[str, Any]]:
        return await run(observation_payloads, database, provider, capability, timeout_seconds=60)

    async def project(day: date) -> Mapping[str, Any]:
        return await run(project_valuations, database, day, apply=True, timeout_seconds=90)

    try:
        max_tick_symbols = max(1, min(100, int(values.get("PUBLIC_ARCHIVE_MAX_TICK_SYMBOLS", "60") or 60)))
    except ValueError:
        max_tick_symbols = 60
    return post_close.ArchiveDeps(
        collector=collector, watch_symbols=watch_symbols, previous_trading_day=previous_day,
        margin_detail_enabled=str(values.get("PUBLIC_ARCHIVE_MARGIN_DETAIL_ENABLED", "false")).strip().lower()
        in {"1", "true", "yes", "on"},
        max_tick_symbols=max_tick_symbols,
        latest_observation_payloads=latest,
        observation_payloads=all_payloads,
        max_gpcw_periods=max(1, int(values.get("PUBLIC_ARCHIVE_TDX_GPCW_MAX_PERIODS", "2") or 2)),
        persist_membership_delta=persist_membership_delta or (
            lambda taxonomy_key, sector_key, members, observed_at:
            run(persist_tdx_membership_delta, database, taxonomy_key, sector_key, members, observed_at, timeout_seconds=60)
        ),
        # Enable on the agreed projection writer only, after shared-stage
        # lease adoption. Merely deploying this code must not start a writer.
        project_valuations=project if str(values.get("DAILY_VALUATION_PROJECTION_ENABLED", "false")).strip().lower()
        in {"1", "true", "yes", "on"} else None,
    )


def collector_loops(
    collector: intraday.CollectorDeps,
    archive: post_close.ArchiveDeps,
    *,
    session_open: Callable[[Any], Awaitable[bool]],
    trading_day: Callable[[date], Awaitable[bool]],
    environ: Mapping[str, str] | None = None,
) -> dict[str, Callable[[], Awaitable[None]]]:
    """The two loop factories, keyed by their runtime task labels."""
    return {
        "public_evidence_capture": lambda: intraday.run_loop(
            collector, session_open=session_open, enabled=env_flags("PUBLIC_EVIDENCE", list(intraday.CADENCES), environ)),
        "post_close_public_archive": lambda: post_close.run_loop(
            archive, trading_day=trading_day,
            enabled=env_flags("PUBLIC_ARCHIVE", [job.key for job in post_close.JOBS], environ,
                              opt_in_keys={"tdx_security_list", "tdx_tipinfo", "tdx_gpcw", "tdx_index_bars",
                                           "tdx_mac_boards", "tdx_limit_pools", "tdx_host_probe"})),
    }


async def run_leased(database: Any, label: str, factory: Callable[[], Awaitable[None]],
                     *, holder_id: uuid.UUID | None = None) -> None:
    """Run one loop only while holding ``background_loop:<label>``."""
    from ..runtime_tasks import supervise_leased_loop  # noqa: PLC0415 - keeps imports light for catalog readers

    holder = holder_id or uuid.uuid4()
    lease_key = f"background_loop:{label}"
    seconds = background_loop_lease_seconds()
    await supervise_leased_loop(
        label, factory,
        lambda: asyncio.to_thread(acquire_runtime_lease, database, lease_key, holder, seconds),
        lambda: asyncio.to_thread(renew_runtime_lease, database, lease_key, holder, seconds),
        lambda: asyncio.to_thread(release_runtime_lease, database, lease_key, holder),
        seconds,
    )


__all__ = [
    "COLLECTOR_TASKS", "build_archive_deps", "build_collector_deps", "collector_loops", "env_flags", "run_leased",
]
