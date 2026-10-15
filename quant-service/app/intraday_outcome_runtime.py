"""Transactional runtime adapter for research-only intraday outcome settlement."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any


@dataclass(frozen=True)
class IntradayOutcomeRuntimeDependencies:
    database: Any
    outcome_cutoff: Callable[[date | None], datetime]
    refresh_attributions: Callable[[Any], int]
    settle: Callable[..., dict[str, Any]]
    horizons: tuple[tuple[str, int], ...]
    direction_for: Callable[[str], int | None]
    metrics_for: Callable[..., Any]
    decimal_or_none: Callable[[Any], Any]
    barrier_spec_type: Callable[[], Any]
    triple_barrier_label: Callable[..., Any]
    persist_barrier_outcome: Callable[..., Any]
    return_decomposition: Callable[..., dict[str, Any]]
    json_safe: Callable[[Any], Any]
    persist_barrier_outcome_batch: Callable[[Any, list[dict[str, Any]]], Any] | None = None
    load_signal_event_ids: Callable[[Any, datetime], list[Any]] | None = None
    signal_batch_size: int = 500


class IntradayOutcomeRuntime:
    """Settle retained signal evidence and its attribution in one transaction."""

    def __init__(self, dependencies: IntradayOutcomeRuntimeDependencies) -> None:
        self._dependencies = dependencies

    def recompute(self, as_of_date: date | None = None) -> dict[str, Any]:
        dependencies = self._dependencies
        cutoff = dependencies.outcome_cutoff(as_of_date)
        if dependencies.load_signal_event_ids is None:
            with dependencies.database.transaction() as connection:
                attribution_backfilled = dependencies.refresh_attributions(connection, cutoff=cutoff)
                result = dependencies.settle(connection, as_of_date, cutoff=cutoff,
                    horizons=dependencies.horizons, direction_for=dependencies.direction_for,
                    metrics_for=dependencies.metrics_for, decimal_or_none=dependencies.decimal_or_none,
                    barrier_spec_type=dependencies.barrier_spec_type, triple_barrier_label=dependencies.triple_barrier_label,
                    persist_barrier_outcome=dependencies.persist_barrier_outcome,
                    return_decomposition=dependencies.return_decomposition, json_safe=dependencies.json_safe,
                    persist_barrier_outcome_batch=dependencies.persist_barrier_outcome_batch)
            return {**result, "attribution_backfilled": attribution_backfilled}

        with dependencies.database.transaction() as connection:
            attribution_backfilled = dependencies.refresh_attributions(connection, cutoff=cutoff)
            # Lightweight repository fakes used by boundary tests expose only
            # transaction identity. Keep the original one-transaction path
            # for them; real psycopg connections always expose ``execute``.
            if not hasattr(connection, "execute"):
                result = dependencies.settle(connection, as_of_date, cutoff=cutoff,
                    horizons=dependencies.horizons, direction_for=dependencies.direction_for,
                    metrics_for=dependencies.metrics_for, decimal_or_none=dependencies.decimal_or_none,
                    barrier_spec_type=dependencies.barrier_spec_type, triple_barrier_label=dependencies.triple_barrier_label,
                    persist_barrier_outcome=dependencies.persist_barrier_outcome,
                    return_decomposition=dependencies.return_decomposition, json_safe=dependencies.json_safe,
                    persist_barrier_outcome_batch=dependencies.persist_barrier_outcome_batch)
                return {**result, "attribution_backfilled": attribution_backfilled}
            work = dependencies.load_signal_event_ids(connection, cutoff)
        chunks = [work[start:start + max(1, dependencies.signal_batch_size)]
                  for start in range(0, len(work), max(1, dependencies.signal_batch_size))]
        if not chunks:
            chunks = [[]]
        result: dict[str, Any] = {"as_of_date": str(as_of_date) if as_of_date else None, "signals": 0,
                                  "skipped_without_entry_price": 0, "outcome_rows": 0, "matured": 0,
                                  "pending": 0, "intraday_horizons": {key: 0 for key, _ in dependencies.horizons}}
        for chunk in chunks:
            with dependencies.database.transaction() as connection:
                item = dependencies.settle(
                    connection, as_of_date, cutoff=cutoff, signal_event_ids=chunk,
                    horizons=dependencies.horizons, direction_for=dependencies.direction_for,
                    metrics_for=dependencies.metrics_for, decimal_or_none=dependencies.decimal_or_none,
                    barrier_spec_type=dependencies.barrier_spec_type, triple_barrier_label=dependencies.triple_barrier_label,
                    persist_barrier_outcome=dependencies.persist_barrier_outcome,
                    return_decomposition=dependencies.return_decomposition, json_safe=dependencies.json_safe,
                    persist_barrier_outcome_batch=dependencies.persist_barrier_outcome_batch)
            for key in ("signals", "skipped_without_entry_price", "outcome_rows", "matured", "pending"):
                result[key] += int(item.get(key) or 0)
            for key, value in (item.get("intraday_horizons") or {}).items():
                result["intraday_horizons"][key] = result["intraday_horizons"].get(key, 0) + int(value or 0)
        return {**result, "attribution_backfilled": attribution_backfilled}


__all__ = ["IntradayOutcomeRuntime", "IntradayOutcomeRuntimeDependencies"]
