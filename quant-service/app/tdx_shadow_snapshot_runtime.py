"""TDX all-A shadow capture and its read-only daily summary."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .all_a_snapshot_sources import compare, project_tdx_snapshot_rows
from .datasources.contracts import CapabilityEvidence
from .error_detail import safe_error_detail
from .minute_cross_section import persist_document
from .public_market_repository import persist_timed_observations
from .public_provider_rate_limits import PublicProviderRateLimited, acquire_public_provider_slot
from .runtime_executors import ExecutorSaturatedError


SHADOW_PROVIDER = "tdx_public"
SHADOW_CAPABILITY = "a_share_minute_cross_section_shadow"
AGREEMENT_PROVIDER = "local_derived"
AGREEMENT_CAPABILITY = "all_a_snapshot_source_agreement"
TDX_READ_TIMEOUT_SECONDS = 20.0
REFERENCE_MAX_AGE_SECONDS = 90.0


def _as_rows_and_metadata(result: CapabilityEvidence | list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if isinstance(result, CapabilityEvidence):
        return list(result.rows), {"warnings": list(result.warnings), "available_at": result.available_at_max}
    return list(result), {}


def _comparison_row(observed_at: datetime, tdx_rows: list[dict[str, Any]],
                   reference: tuple[datetime, list[dict[str, Any]]] | None) -> dict[str, Any]:
    row: dict[str, Any] = {
        "provider_key": AGREEMENT_PROVIDER,
        "capability": AGREEMENT_CAPABILITY,
        "observed_at": observed_at.isoformat(),
        "research_only": True,
        "live_effect": "none",
    }
    if reference is None:
        return {**row, "status": "no_reference", "reference_observed_at": None, "comparison": None}
    reference_at, reference_rows = reference
    seconds_apart = abs((observed_at - reference_at).total_seconds())
    return {
        **row,
        "status": "compared",
        "reference_observed_at": reference_at.isoformat(),
        "comparison": compare(reference_rows, tdx_rows, seconds_apart),
    }


def persist_shadow_capture(database: Any, observed_at: datetime, rows: list[dict[str, Any]],
                           metadata: Mapping[str, Any], comparison_row: Mapping[str, Any]) -> dict[str, Any]:
    """Write the shadow document, then its derived comparison in that order."""
    document = persist_document(
        database, observed_at, rows, metadata,
        provider=SHADOW_PROVIDER, capability=SHADOW_CAPABILITY,
    )
    try:
        comparison_stored = persist_timed_observations(
            database, AGREEMENT_PROVIDER, AGREEMENT_CAPABILITY,
            [{**dict(comparison_row), "effective_at": observed_at.isoformat(),
              "available_at": datetime.now(timezone.utc).isoformat()}],
        )
    except Exception as error:  # noqa: BLE001 - the document remains useful when its derived row fails
        return {"document": document, "comparison": {"status": "failed", "error": safe_error_detail(str(error), 200)}}
    return {"document": document, "comparison": {"status": "stored", "rows": comparison_stored}}


@dataclass(frozen=True)
class TdxShadowSnapshotDependencies:
    fetch_snapshot: Callable[[], Awaitable[CapabilityEvidence | list[dict[str, Any]]]]
    session_open: Callable[[datetime], Awaitable[bool | tuple[bool, str]]]
    run_database: Callable[..., Awaitable[Any]]
    database: Any
    recent_reference: Callable[[datetime], tuple[datetime, list[dict[str, Any]]] | None]
    acquire_slot: Callable[..., Awaitable[None]] = acquire_public_provider_slot
    persist_capture: Callable[[Any, datetime, list[dict[str, Any]], Mapping[str, Any], Mapping[str, Any]], dict[str, Any]] = persist_shadow_capture


@dataclass
class TdxShadowSnapshotRuntime:
    deps: TdxShadowSnapshotDependencies
    _read_task: asyncio.Task[CapabilityEvidence | list[dict[str, Any]]] | None = field(default=None, init=False)

    @staticmethod
    def _consume_late_exception(task: asyncio.Task[Any]) -> None:
        if task.cancelled():
            return
        try:
            task.exception()
        except BaseException:
            pass

    async def capture(self, observed_at: datetime | None = None) -> dict[str, Any]:
        observed = observed_at or datetime.now(timezone.utc)
        session_result = await self.deps.session_open(observed)
        active = bool(session_result[0]) if isinstance(session_result, tuple) else bool(session_result)
        if not active:
            return {"status": "outside_session", "stored": 0}
        if self._read_task is not None:
            if not self._read_task.done():
                return {"status": "busy", "stored": 0}
            self._read_task = None
        try:
            await self.deps.acquire_slot(SHADOW_PROVIDER, max_wait_seconds=0)
        except PublicProviderRateLimited as error:
            return {"status": "rate_limited", "error": str(error), "stored": 0}
        task = asyncio.create_task(self.deps.fetch_snapshot(), name="tdx-shadow-read")
        self._read_task = task
        try:
            result = await asyncio.wait_for(asyncio.shield(task), timeout=TDX_READ_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            task.add_done_callback(self._consume_late_exception)
            return {"status": "timeout", "stored": 0}
        except Exception as error:  # noqa: BLE001 - capture outcome is bounded and secret-free
            self._read_task = None
            return {"status": "failed", "error": safe_error_detail(str(error), 300), "stored": 0}
        self._read_task = None
        rows, metadata = _as_rows_and_metadata(result)
        projected = project_tdx_snapshot_rows(rows, observed)
        document_metadata = {
            **metadata,
            "provider": SHADOW_PROVIDER,
            "capability": SHADOW_CAPABILITY,
            "source": "tdx_public_all_a_snapshot",
            "research_only": True,
            "live_effect": "none",
        }
        reference = self.deps.recent_reference(observed)
        if reference is not None and abs((observed - reference[0]).total_seconds()) > REFERENCE_MAX_AGE_SECONDS:
            reference = None
        comparison_row = _comparison_row(observed, projected, reference)
        try:
            stored = await self.deps.run_database(
                self.deps.persist_capture, self.deps.database, observed, projected,
                document_metadata, comparison_row, timeout_seconds=60,
            )
        except ExecutorSaturatedError as error:
            return {"status": "database_busy", "error": str(error), "stored": 0}
        except Exception as error:  # noqa: BLE001 - the next minute retries the failed write
            return {"status": "failed", "error": safe_error_detail(str(error), 300), "stored": 0}
        return {
            "status": "completed", "stored": int(stored.get("document", {}).get("rows") or 0),
            "provider": SHADOW_PROVIDER, "capability": SHADOW_CAPABILITY,
            "comparison": stored.get("comparison"),
        }


async def run_tdx_shadow_snapshot_loop(
    runtime: TdxShadowSnapshotRuntime, *, interval_seconds: int = 60, log: Callable[[str], None] = print,
    sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
) -> None:
    while True:
        result = await runtime.capture()
        if result.get("status") not in {"completed", "outside_session"}:
            suffix = f": {result.get('error')}" if result.get("error") else ""
            log(f"tdx_all_a_shadow {result['status']}{suffix}")
        await sleep(max(10, min(300, int(interval_seconds))))


__all__ = [
    "AGREEMENT_CAPABILITY", "AGREEMENT_PROVIDER", "SHADOW_CAPABILITY", "SHADOW_PROVIDER",
    "TdxShadowSnapshotDependencies", "TdxShadowSnapshotRuntime",
    "persist_shadow_capture", "run_tdx_shadow_snapshot_loop",
]
