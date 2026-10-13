"""TDX all-A shadow capture and its read-only daily summary."""

from __future__ import annotations

import argparse
import asyncio
import time as clock_time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
import json
from statistics import median
from typing import Any
from zoneinfo import ZoneInfo

from .all_a_snapshot_sources import compare, project_tdx_snapshot_rows, tdx_snapshot_completeness
from .datasources.contracts import CapabilityEvidence
from .datasources.catalog import SESSION_AGREEMENT_RULES
from .error_detail import safe_error_detail
from .intraday_clock import continuous_auction_bounds
from .market_session_repository import market_observation_session
from .minute_cross_section import persist_document
from .public_market_repository import persist_timed_observations
from .public_provider_rate_limits import PublicProviderRateLimited, acquire_public_provider_slot
from .runtime_executors import ExecutorSaturatedError
from .intraday_schedule import seconds_until_slot


SHADOW_PROVIDER = "tdx_public"
SHADOW_CAPABILITY = "a_share_minute_cross_section_shadow"
AGREEMENT_PROVIDER = "local_derived"
AGREEMENT_CAPABILITY = "all_a_snapshot_source_agreement"
TDX_READ_TIMEOUT_SECONDS = 20.0
SESSION_GATE_TIMEOUT_SECONDS = 10.0
# The shadow boundary has one worker and no queue: a step that outlives its deadline keeps the worker
# until its thread returns, and the next step is refused. Both are the same outcome for a capture.
DATABASE_UNAVAILABLE = (ExecutorSaturatedError, TimeoutError)
REFERENCE_MAX_AGE_SECONDS = 90.0
SUMMARY_STATEMENT_TIMEOUT_SECONDS = 10.0
CN_TZ = ZoneInfo("Asia/Shanghai")
SESSION_RULE = SESSION_AGREEMENT_RULES[(SHADOW_PROVIDER, "quote.all_a_snapshot")]
DATA_GAP_GROUPS = (
    (5, "under_5"), (10, "5_10"), (15, "10_15"), (20, "15_20"),
    (30, "20_30"), (45, "30_45"), (60, "45_60"), (float("inf"), "60_and_more"),
)


def _database_busy(error: BaseException) -> dict[str, Any]:
    return {"status": "database_busy", "error": str(error) or type(error).__name__, "stored": 0}


def _as_rows_and_metadata(result: CapabilityEvidence | list[dict[str, Any]],
                          observed_at: datetime) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if isinstance(result, CapabilityEvidence):
        return list(result.rows), {"warnings": list(result.warnings), "available_at": result.available_at_max}
    return list(result), {"available_at": observed_at}


def _comparison_row(observed_at: datetime, tdx_rows: list[dict[str, Any]],
                   reference: tuple[datetime, list[dict[str, Any]]] | None,
                   completeness: Mapping[str, Any], shadow_data_at: datetime) -> dict[str, Any]:
    row: dict[str, Any] = {
        "provider_key": AGREEMENT_PROVIDER,
        "capability": AGREEMENT_CAPABILITY,
        "observed_at": observed_at.isoformat(),
        "completeness": dict(completeness),
        "shadow_data_at": shadow_data_at.isoformat(),
        "research_only": True,
        "live_effect": "none",
    }
    if reference is None:
        return {
            **row, "status": "incomplete" if not completeness["complete"] else "no_reference",
            "reference_observed_at": None, "reference_data_at": None,
            "data_seconds_apart": None, "same_continuous_segment": False, "comparison": None,
        }
    reference_at, reference_rows = reference
    age_seconds = reference_rows[0]["snapshot_metadata"].get("age_seconds")
    if not completeness["complete"]:
        if age_seconds is None:
            return {
                **row, "status": "incomplete", "reference_observed_at": reference_at.isoformat(),
                "reference_data_at": None, "data_seconds_apart": None,
                "same_continuous_segment": False, "comparison": None,
            }
        reference_data_at = reference_at - timedelta(seconds=float(age_seconds))
        data_seconds_apart = abs((shadow_data_at - reference_data_at).total_seconds())
        shadow_bounds = continuous_auction_bounds(shadow_data_at)
        reference_bounds = continuous_auction_bounds(reference_data_at)
        return {
            **row, "status": "incomplete", "reference_observed_at": reference_at.isoformat(),
            "reference_data_at": reference_data_at.isoformat(), "data_seconds_apart": data_seconds_apart,
            "same_continuous_segment": shadow_bounds is not None and shadow_bounds == reference_bounds,
            "comparison": None,
        }
    seconds_apart = abs((observed_at - reference_at).total_seconds())
    comparison = compare(reference_rows, tdx_rows, seconds_apart)
    if age_seconds is None:
        return {
            **row, "status": "compared",
            "reference_observed_at": reference_at.isoformat(), "reference_data_at": None,
            "data_seconds_apart": None, "same_continuous_segment": False, "comparison": comparison,
        }
    reference_data_at = reference_at - timedelta(seconds=float(age_seconds))
    data_seconds_apart = abs((shadow_data_at - reference_data_at).total_seconds())
    shadow_bounds = continuous_auction_bounds(shadow_data_at)
    reference_bounds = continuous_auction_bounds(reference_data_at)
    same_continuous_segment = shadow_bounds is not None and shadow_bounds == reference_bounds
    return {
        **row,
        "status": "compared",
        "reference_observed_at": reference_at.isoformat(),
        "reference_data_at": reference_data_at.isoformat(),
        "data_seconds_apart": data_seconds_apart,
        "same_continuous_segment": same_continuous_segment,
        "comparison": comparison,
    }


def persist_shadow_capture(database: Any, observed_at: datetime, rows: list[dict[str, Any]],
                           metadata: Mapping[str, Any], comparison_row: Mapping[str, Any]) -> dict[str, Any]:
    """Write the shadow document, then its derived comparison in that order."""
    document = persist_document(
        database, observed_at, rows, metadata,
        provider=SHADOW_PROVIDER, capability=SHADOW_CAPABILITY,
    )
    comparison_stored = persist_timed_observations(
        database, AGREEMENT_PROVIDER, AGREEMENT_CAPABILITY,
        [{**dict(comparison_row), "effective_at": observed_at.isoformat(),
          "available_at": datetime.now(timezone.utc).isoformat()}],
    )
    return {"document": document, "comparison": {"status": "stored", "rows": comparison_stored}}


@dataclass(frozen=True)
class TdxShadowSnapshotDependencies:
    fetch_snapshot: Callable[[], Awaitable[CapabilityEvidence | list[dict[str, Any]]]]
    run_database: Callable[..., Awaitable[Any]]
    database: Any
    recent_reference: Callable[[datetime], tuple[datetime, list[dict[str, Any]]] | None]
    storage_allowed: Callable[[], Awaitable[tuple[bool, dict[str, Any]]]]
    roster_total: Callable[[], Awaitable[int | None]]
    acquire_slot: Callable[..., Awaitable[None]] = acquire_public_provider_slot
    session_gate: Callable[[Any, datetime], tuple[bool, str]] = market_observation_session
    persist_capture: Callable[[Any, datetime, list[dict[str, Any]], Mapping[str, Any], Mapping[str, Any]], dict[str, Any]] = persist_shadow_capture


@dataclass
class TdxShadowSnapshotRuntime:
    deps: TdxShadowSnapshotDependencies
    _read_task: asyncio.Task[CapabilityEvidence | list[dict[str, Any]]] | None = field(default=None, init=False)
    _roster_totals: dict[date, int] = field(default_factory=dict, init=False)

    @staticmethod
    def _consume_late_exception(task: asyncio.Task[Any]) -> None:
        if task.cancelled():
            return
        try:
            task.exception()
        except BaseException:
            pass

    async def _roster_total(self, observed_at: datetime) -> int | None:
        trade_date = observed_at.astimezone(CN_TZ).date()
        if trade_date in self._roster_totals:
            return self._roster_totals[trade_date]
        try:
            total = await self.deps.roster_total()
        except DATABASE_UNAVAILABLE:
            return None
        if total is not None:
            self._roster_totals[trade_date] = total
        return total

    async def capture(self, observed_at: datetime | None = None) -> dict[str, Any]:
        observed = observed_at or datetime.now(timezone.utc)
        try:
            active, _reason = await self.deps.run_database(
                self.deps.session_gate, self.deps.database, observed, timeout_seconds=SESSION_GATE_TIMEOUT_SECONDS,
            )
        except DATABASE_UNAVAILABLE as error:
            return _database_busy(error)
        if not active:
            return {"status": "outside_session", "stored": 0}
        if self._read_task is not None:
            if not self._read_task.done():
                return {"status": "busy", "stored": 0}
            self._read_task = None
        try:
            allowed, storage = await self.deps.storage_allowed()
        except DATABASE_UNAVAILABLE as error:
            return _database_busy(error)
        if not allowed:
            return {"status": "storage_paused", "storage": storage, "stored": 0}
        try:
            await self.deps.acquire_slot(SHADOW_PROVIDER, max_wait_seconds=0)
        except PublicProviderRateLimited as error:
            return {"status": "rate_limited", "error": str(error), "stored": 0}
        task = asyncio.create_task(self.deps.fetch_snapshot(), name="tdx-shadow-read")
        self._read_task = task
        task.add_done_callback(self._consume_late_exception)
        try:
            done, _ = await asyncio.wait({task}, timeout=TDX_READ_TIMEOUT_SECONDS)
            if not done:
                return {"status": "timeout", "stored": 0}
            result = task.result()
        except Exception as error:  # noqa: BLE001 - capture outcome is bounded and secret-free
            self._read_task = None
            return {"status": "failed", "error": safe_error_detail(str(error), 300), "stored": 0}
        self._read_task = None
        rows, metadata = _as_rows_and_metadata(result, observed)
        projected = project_tdx_snapshot_rows(rows, observed)
        completeness = tdx_snapshot_completeness(
            len(rows), metadata.get("warnings") or (), await self._roster_total(observed),
        )
        document_metadata = {
            **metadata,
            "provider": SHADOW_PROVIDER,
            "capability": SHADOW_CAPABILITY,
            "source": "tdx_public_all_a_snapshot",
            "completeness": completeness,
            "research_only": True,
            "live_effect": "none",
        }
        reference = self.deps.recent_reference(observed)
        if reference is not None and abs((observed - reference[0]).total_seconds()) > REFERENCE_MAX_AGE_SECONDS:
            reference = None
        comparison_row = _comparison_row(
            observed, projected, reference, completeness, metadata["available_at"],
        )
        try:
            stored = await self.deps.run_database(
                self.deps.persist_capture, self.deps.database, observed, projected,
                document_metadata, comparison_row, timeout_seconds=60,
            )
        except DATABASE_UNAVAILABLE as error:
            return _database_busy(error)
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
    clock: Callable[[], float] = clock_time.time,
) -> None:
    interval = max(10, min(300, int(interval_seconds)))
    while True:
        now = clock()
        slot_wait = seconds_until_slot(now, interval, 52)
        slot_at = now + slot_wait
        await sleep(slot_wait)
        result = await runtime.capture()
        if result.get("status") not in {"completed", "outside_session"}:
            suffix = f": {result.get('error')}" if result.get("error") else ""
            log(f"tdx_all_a_shadow {result['status']}{suffix}")
        if clock() >= slot_at + interval:
            log("tdx_all_a_shadow capture overran its slot; skipped to the next slot")


def _day_bounds(trade_date: date) -> tuple[datetime, datetime]:
    start = datetime.combine(trade_date, time(0), CN_TZ)
    return start, start + timedelta(days=1)


def _payload(value: Any) -> dict[str, Any]:
    return json.loads(value) if isinstance(value, str) else dict(value)


def _session_minutes(trade_date: date) -> int:
    return 0 if trade_date.weekday() >= 5 else 255


def _data_gap_group(seconds: float) -> str:
    return next(label for limit, label in DATA_GAP_GROUPS if seconds < limit)


def day_summary(database: Any, trade_date: date, *, statement_timeout_seconds: float = SUMMARY_STATEMENT_TIMEOUT_SECONDS) -> dict[str, Any]:
    """Read one day's shadow documents and comparisons with a bounded SQL statement."""
    start, end = _day_bounds(trade_date)
    with database.transaction() as connection:
        connection.execute(f"SET LOCAL statement_timeout = '{int(statement_timeout_seconds * 1000)}ms'")
        documents = connection.execute(
            """SELECT effective_at,(payload->>'rows')::int AS rows FROM quant.raw_market_observations
               WHERE provider_key='tdx_public' AND capability='a_share_minute_cross_section_shadow'
                 AND symbol IS NULL AND effective_at>=%s AND effective_at<%s ORDER BY effective_at""",
            (start, end),
        ).fetchall()
        comparisons = connection.execute(
            """SELECT effective_at,normalized FROM quant.raw_market_observations
               WHERE provider_key='local_derived' AND capability='all_a_snapshot_source_agreement'
                 AND symbol IS NULL AND effective_at>=%s AND effective_at<%s ORDER BY effective_at""",
            (start, end),
        ).fetchall()
    comparison_payloads = [_payload(row["normalized"]) for row in comparisons]
    compared = [item for item in comparison_payloads if item.get("status") == "compared"]
    metrics = [item["comparison"] for item in compared]
    status_counts = {status: sum(item.get("status") == status for item in comparison_payloads)
                     for status in ("compared", "incomplete", "no_reference")}
    judged = [item for item in compared
              if item.get("data_seconds_apart") is not None
              and item.get("data_seconds_apart") <= SESSION_RULE["max_data_gap_seconds"]
              and item.get("same_continuous_segment") is True]
    agreeing = [item for item in judged
                if item["comparison"]["price"].get("within_0_5_pct") is not None
                and item["comparison"]["price"]["within_0_5_pct"] >= SESSION_RULE["min_price_within_0_5_pct"]]
    judged_agreement_share = len(agreeing) / len(judged) if judged else None
    gap_groups: dict[str, list[dict[str, Any]]] = {}
    for item in compared:
        seconds = item.get("data_seconds_apart")
        if seconds is not None:
            gap_groups.setdefault(_data_gap_group(float(seconds)), []).append(item)

    def group_medians(items: list[dict[str, Any]]) -> dict[str, Any]:
        group_metrics = [item["comparison"] for item in items]
        return {
            "minutes": len(items),
            "median_data_seconds_apart": median_or_none([float(item["data_seconds_apart"]) for item in items]),
            "median_price_within_0_5_pct": median_or_none(metric_values_from(group_metrics, ("price", "within_0_5_pct"))),
            "median_volume_ratio": median_or_none(metric_values_from(group_metrics, ("volume", "median_ratio"))),
            "median_turnover_ratio": median_or_none(metric_values_from(group_metrics, ("turnover", "median_ratio"))),
        }

    def metric_values_from(values: list[dict[str, Any]], path: tuple[str, ...]) -> list[float]:
        result = []
        for item in values:
            value: Any = item
            for key in path:
                value = value[key]
            if value is not None:
                result.append(float(value))
        return result
    gaps = [
        {"relative_gap": item["price"]["largest_relative_gap"],
         "symbol": item["price"]["largest_relative_gap_symbol"]}
        for item in metrics if item["price"].get("largest_relative_gap") is not None
    ]

    def metric_values(path: tuple[str, ...]) -> list[float]:
        values = []
        for item in metrics:
            value: Any = item
            for key in path:
                value = value[key]
            if value is not None:
                values.append(float(value))
        return values

    def median_or_none(values: list[float]) -> float | None:
        return median(values) if values else None

    def min_or_none(values: list[float]) -> float | None:
        return min(values) if values else None

    return {
        "trade_date": trade_date.isoformat(),
        "shadow_documents": len(documents),
        "stored_minutes": len(documents),
        "expected_minutes": _session_minutes(trade_date),
        "session_minutes": _session_minutes(trade_date),
        "minutes_without_document": max(0, _session_minutes(trade_date) - len(documents)),
        "lowest_rows_per_document": min((int(row["rows"]) for row in documents), default=None),
        "median_rows_per_document": median_or_none([float(row["rows"]) for row in documents]),
        "documents_without_comparison": max(0, len(documents) - len(comparison_payloads)),
        "minutes_compared": len(compared),
        "no_reference_minutes": sum(item.get("status") == "no_reference" for item in comparison_payloads),
        "status_counts": status_counts,
        "incomplete_minutes": status_counts["incomplete"],
        "not_judged_minutes": len(comparison_payloads) - len(judged),
        "rows_without_data_times": sum(item.get("data_seconds_apart") is None for item in comparison_payloads),
        "judged_minutes": len(judged),
        "judged_minutes_agree": len(agreeing),
        "judged_agreement_share": judged_agreement_share,
        "session_passes": len(judged) >= SESSION_RULE["min_judged_minutes"] and
        judged_agreement_share is not None and judged_agreement_share >= SESSION_RULE["min_agreement_share"],
        "agreement_rule": dict(SESSION_RULE),
        "gap_group_medians": {label: group_medians(gap_groups[label]) for _limit, label in DATA_GAP_GROUPS if label in gap_groups},
        "largest_price_gap": max(gaps, key=lambda item: item["relative_gap"], default=None),
        "median_price_within_one_tick": median_or_none(metric_values(("price", "within_one_tick"))),
        "worst_price_within_one_tick": min_or_none(metric_values(("price", "within_one_tick"))),
        "median_price_within_0_1_pct": median_or_none(metric_values(("price", "within_0_1_pct"))),
        "worst_price_within_0_1_pct": min_or_none(metric_values(("price", "within_0_1_pct"))),
        "median_price_within_0_5_pct": median_or_none(metric_values(("price", "within_0_5_pct"))),
        "worst_price_within_0_5_pct": min_or_none(metric_values(("price", "within_0_5_pct"))),
        "median_volume_ratio": median_or_none(metric_values(("volume", "median_ratio"))),
        "median_turnover_ratio": median_or_none(metric_values(("turnover", "median_ratio"))),
        "median_seconds_apart": median_or_none(metric_values(("seconds_apart",))),
        "largest_seconds_apart": max(metric_values(("seconds_apart",)), default=None),
        "research_only": True,
        "live_effect": "none",
    }


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="summarize the TDX all-A shadow stream")
    parser.add_argument("--trade-date", required=True, type=date.fromisoformat)
    args = parser.parse_args(argv)
    from .database import Database

    database = Database()
    try:
        print(json.dumps(day_summary(database, args.trade_date), ensure_ascii=False, sort_keys=True, default=str))
    finally:
        database.close()


if __name__ == "__main__":  # pragma: no cover
    main()


__all__ = [
    "AGREEMENT_CAPABILITY", "AGREEMENT_PROVIDER", "SHADOW_CAPABILITY", "SHADOW_PROVIDER",
    "TdxShadowSnapshotDependencies", "TdxShadowSnapshotRuntime", "day_summary", "main",
    "persist_shadow_capture", "run_tdx_shadow_snapshot_loop",
]
