"""Local health-payload assembly for the loopback research service."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import os
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo


class DatabaseUnavailableError(RuntimeError):
    """A health probe could not reach the local repository."""


def async_pool_stall_reason(pool: dict[str, Any] | None) -> str | None:
    """Name the async pool's wedged state, or return None when it is usable.

    The sync ping alone cannot see this.  Twice on 2026-09-17 the db-tunnel
    dropped its forwarded connections, the sync pool reconnected, and the async
    pool did not: it sat at ``pool_size`` 0 with a full 64-deep waiting queue
    for three quarters of an hour while every async route failed and the
    container still reported healthy, because ``ping`` never touches it.

    Holding no connections at all *while* callers are queued is what makes this
    specific.  A pool that is merely saturated carries its connections and
    reports ``pool_size`` at ``max_size``, and a pool still filling on startup
    has nobody waiting on it.
    """
    if not pool or not pool.get("open"):
        return None
    if int(pool.get("pool_size") or 0) > 0 or int(pool.get("waiting") or 0) <= 0:
        return None
    return (
        f"async pool holds no connections while {pool.get('waiting')} requests wait "
        f"(min_size={pool.get('min_size')}, max_size={pool.get('max_size')})"
    )


@dataclass(frozen=True)
class HealthDependencies:
    database: Any
    post_close_lease_key: str
    background_loop_lease_seconds: Callable[[], int]
    data_directory: Callable[[], Path]
    resource_status: Callable[[Path], dict[str, Any]]
    public_http_client_status: Callable[[], dict[str, Any]]
    alert_http_client_status: Callable[[], dict[str, Any]]
    provider_http_client_status: Callable[[], dict[str, Any]]
    remote_archive_http_client_status: Callable[[], dict[str, Any]]
    network_status: Callable[[], dict[str, Any]]
    provider_request_reservation_status: Callable[[], dict[str, Any]]
    runtime_executor_status: Callable[[], dict[str, Any]]
    super_get_executor_status: Callable[[], dict[str, Any]]
    provider_status: Callable[[], list[dict[str, Any]]]
    free_provider_status: Callable[[], list[dict[str, Any]]]
    realtime_market_session: Callable[[], tuple[bool, str]]
    board_curve_session: Callable[[], tuple[bool, str]]
    scan_interval_seconds: Callable[[], int]
    effective_scan_interval_seconds: Callable[[int, datetime], int]
    high_frequency_window: Callable[[datetime], bool]
    board_curve_enabled: Callable[[], bool]
    board_curve_retention_days: Callable[[], int]
    board_rotation_retention_days: Callable[[], int]
    set_db_pool_gauge: Callable[[dict[str, Any]], None]
    set_open_circuit_gauge: Callable[[int], None]
    async_database_pool_status: Callable[[], dict[str, Any]] | None = None
    async_pool_watchdog_status: Callable[[], dict[str, Any]] | None = None
    research_storage_governance: Callable[[Any], dict[str, Any]] | None = None
    background_loop_status: Callable[[], dict[str, dict[str, Any]]] | None = None
    runtime_task_contracts: Callable[[], list[dict[str, Any]]] | None = None
    optional_background_tasks: Callable[[], dict[str, bool]] | None = None
    daily_control_plane_status: Callable[[], dict[str, Any]] | None = None
    live_session_acceptance_status: Callable[[], dict[str, Any]] | None = None
    release_metadata: Callable[[], dict[str, str | None]] | None = None
    post_close_runtime_status: Callable[[], dict[str, Any]] | None = None
    write_boundary_status: Callable[[], dict[str, Any]] | None = None
    raw_overflow_status: Callable[[Any], dict[str, Any]] | None = None
    owner_storage_status: Callable[[Any], dict[str, Any]] | None = None
    owner_deploy_status: Callable[[Any], dict[str, Any]] | None = None


def runtime_loops_with_lease_heartbeats(
    runtime_loops: dict[str, dict[str, Any]], background_loop_leases: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Attach durable lease renewal evidence without relabelling lifecycle state.

    ``updated_at`` is deliberately the last lifecycle transition.  A long
    lived loop therefore needs a separate heartbeat field: lease renewal is a
    local, cross-process proof that its current owner is still active.
    """
    leases = {
        str(row.get("lease_key") or "").removeprefix("background_loop:"): row
        for row in background_loop_leases
    }
    result: dict[str, dict[str, Any]] = {}
    for label, item in runtime_loops.items():
        lease = leases.get(label)
        result[label] = {
            **dict(item),
            "lease_heartbeat_at": lease.get("updated_at") if lease else None,
            "lease_expires_at": lease.get("expires_at") if lease else None,
        }
    return result


class HealthEvidenceCache:
    """Serve the slow, slow-changing health sections from memory.

    On the peer every database round trip crosses the SSH tunnel (~100 ms), so
    the full payload - leases, owner storage, research storage, raw-overflow
    offsets, the daily control plane - cost 2-3 s per call and more than 15 s
    whenever the tunnel hiccupped; the session guard then restarted a working
    service mid-session (2026-09-22).  Liveness (the sync-pool ping, the async
    pool, executors) stays live on every call.  The heavy part is refreshed in
    one background thread once older than ``ttl_seconds`` and recomputed inline
    only when missing or older than ``max_stale_seconds``.
    """

    def __init__(self, ttl_seconds: float = 30.0, max_stale_seconds: float = 300.0) -> None:
        import threading
        self.ttl_seconds, self.max_stale_seconds = ttl_seconds, max_stale_seconds
        self._lock = threading.Lock()
        self._value: dict[str, Any] | None = None
        self._at: float | None = None
        self._refreshing = False

    def get(self, compute: Callable[[], dict[str, Any]]) -> tuple[dict[str, Any], float]:
        import threading
        import time as _time
        now = _time.monotonic()
        with self._lock:
            value, at = self._value, self._at
            age = None if at is None else now - at
            if value is not None and age is not None and age < self.max_stale_seconds:
                if age >= self.ttl_seconds and not self._refreshing:
                    self._refreshing = True
                    threading.Thread(target=self._refresh, args=(compute,), daemon=True,
                                     name="health-evidence-refresh").start()
                return value, age
        fresh = compute()
        with self._lock:
            self._value, self._at = fresh, _time.monotonic()
        return fresh, 0.0

    def _refresh(self, compute: Callable[[], dict[str, Any]]) -> None:
        import time as _time
        try:
            fresh = compute()
            with self._lock:
                self._value, self._at = fresh, _time.monotonic()
        except Exception:  # noqa: BLE001 - the next call retries; a stale value ages out
            pass
        finally:
            with self._lock:
                self._refreshing = False


def health_payload(deps: HealthDependencies, cache: HealthEvidenceCache | None = None) -> dict[str, Any]:
    """Build health evidence from local state only; no market request occurs."""
    try:
        deps.database.ping()
    except Exception as error:  # noqa: BLE001 - endpoint translates to an HTTP health failure
        raise DatabaseUnavailableError(str(error)) from error
    async_pool = deps.async_database_pool_status() if deps.async_database_pool_status else None
    stalled = async_pool_stall_reason(async_pool)
    if stalled is not None:
        watchdog = deps.async_pool_watchdog_status() if deps.async_pool_watchdog_status else {}
        pending = watchdog.get("confirmations_before_recovery")
        seen = watchdog.get("consecutive_stalls")
        raise DatabaseUnavailableError(
            f"{stalled}; watchdog has seen this {seen} of {pending} times before replacing the pool"
            if pending is not None else stalled
        )
    local_now = datetime.now(timezone.utc).astimezone(ZoneInfo("Asia/Shanghai"))
    session_active, session_reason = deps.realtime_market_session()
    board_session_active, board_session_reason = deps.board_curve_session()
    normal_interval = deps.scan_interval_seconds()
    loop_lease_seconds = deps.background_loop_lease_seconds()
    pool = deps.database.pool_status()
    deps.set_db_pool_gauge(pool)
    if cache is None:
        heavy, heavy_age = _heavy_evidence(deps), 0.0
    else:
        heavy, heavy_age = cache.get(lambda: _heavy_evidence(deps))
    deps.set_open_circuit_gauge(int(heavy["open_circuits"]))
    background_leases = heavy["background_leases"]
    post_close_lease = heavy["post_close_lease"]
    runtime_loops = deps.background_loop_status() if deps.background_loop_status else {}
    return {
        "status": "ok", "service": "quant-research", "database_pool": pool,
        "build": deps.release_metadata() if deps.release_metadata else {},
        "async_database_pool": async_pool,
        "async_pool_watchdog": deps.async_pool_watchdog_status() if deps.async_pool_watchdog_status else {},
        "evidence_age_seconds": round(heavy_age, 1),
        "resources": heavy["resources"],
        "owner_storage": heavy["owner_storage"],
        "owner_deploy": heavy["owner_deploy"],
        "runtime_leases": {
            "background_loop_lease_seconds": loop_lease_seconds,
            "post_close_refresh": {
                "active": bool(post_close_lease),
                "expires_at": post_close_lease["expires_at"] if post_close_lease else None,
                "updated_at": post_close_lease["updated_at"] if post_close_lease else None,
            },
            "background_loops": background_leases,
        },
        "runtime_loops": runtime_loops_with_lease_heartbeats(runtime_loops, background_leases),
        "runtime_tasks": {
            "post_close_refresh": deps.post_close_runtime_status() if deps.post_close_runtime_status else {},
            "raw_overflow_archive": heavy["raw_overflow_archive"],
        },
        "runtime_task_contracts": deps.runtime_task_contracts() if deps.runtime_task_contracts else [],
        "optional_background_tasks": deps.optional_background_tasks() if deps.optional_background_tasks else {},
        "daily_control_plane": heavy["daily_control_plane"],
        "live_session_acceptance": heavy["live_session_acceptance"],
        "http_clients": {
            "public_market": deps.public_http_client_status(), "feishu_alert": deps.alert_http_client_status(),
            "tushare_provider": deps.provider_http_client_status(),
            "remote_analyst_archive": deps.remote_archive_http_client_status(),
        },
        "network": deps.network_status(),
        "write_boundary": deps.write_boundary_status() if deps.write_boundary_status else {},
        "provider_rate_limits": deps.provider_request_reservation_status(),
        "blocking_executors": {**deps.runtime_executor_status(), "super_get": deps.super_get_executor_status()},
        "market_providers": heavy["market_providers"],
        "intraday_automation": {
            "enabled": normal_interval >= 30, "normal_scan_interval_seconds": normal_interval,
            "effective_scan_interval_seconds": deps.effective_scan_interval_seconds(normal_interval, local_now),
            "special_window_active": deps.high_frequency_window(local_now), "special_window_scan_interval_seconds": 10,
            "board_curve_enabled": deps.board_curve_enabled(),
            "board_curve_interval_seconds": 60, "board_curve_retention_days": deps.board_curve_retention_days(),
            "board_rotation_retention_days": deps.board_rotation_retention_days(),
            "board_curve_session_active": board_session_active, "board_curve_session_reason": board_session_reason,
            "session_active": session_active, "session_reason": session_reason, "timezone": "Asia/Shanghai",
        },
    }


def _heavy_evidence(deps: HealthDependencies) -> dict[str, Any]:
    """The database-heavy, slow-changing sections of the health payload."""
    with deps.database.transaction() as connection:
        open_circuits = connection.execute(
            "SELECT count(*)::int AS count FROM quant.provider_health WHERE circuit_open_until > now()"
        ).fetchone()["count"]
        post_close_lease = connection.execute(
            """SELECT expires_at,updated_at FROM quant.runtime_leases
                 WHERE lease_key=%s AND expires_at > now()""",
            (deps.post_close_lease_key,),
        ).fetchone()
        background_loop_leases = connection.execute(
            """SELECT lease_key,expires_at,updated_at FROM quant.runtime_leases
                 WHERE lease_key LIKE 'background_loop:%' AND expires_at > now()
                 ORDER BY lease_key"""
        ).fetchall()
        owner_storage = deps.owner_storage_status(connection) if deps.owner_storage_status else None
        owner_deploy = deps.owner_deploy_status(connection) if deps.owner_deploy_status else None
    resources = deps.resource_status(deps.data_directory())
    if deps.research_storage_governance is not None:
        resources["research_storage"] = deps.research_storage_governance(deps.database)
    return {
        "open_circuits": int(open_circuits),
        "post_close_lease": dict(post_close_lease) if post_close_lease else None,
        "background_leases": [dict(row) for row in background_loop_leases],
        "owner_storage": owner_storage, "owner_deploy": owner_deploy, "resources": resources,
        "raw_overflow_archive": deps.raw_overflow_status(deps.database) if deps.raw_overflow_status else {},
        "daily_control_plane": deps.daily_control_plane_status() if deps.daily_control_plane_status else {},
        "live_session_acceptance": deps.live_session_acceptance_status() if deps.live_session_acceptance_status else {},
        "market_providers": [*deps.provider_status(), *deps.free_provider_status()],
    }


__all__ = [
    "DatabaseUnavailableError", "HealthDependencies", "HealthEvidenceCache", "async_pool_stall_reason", "health_payload",
    "runtime_loops_with_lease_heartbeats",
]
