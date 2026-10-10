"""Resolve a capability to rows, trying its bound sources in catalog order.

The composition root binds implementations (``resolver.bind(source,
capability, fetch)``); consumers only say *what* they need::

    result = await resolver.fetch("limits.limit_up_pool", trade_date=today)
    result.rows, result.source, result.attempts

Order comes from the catalog priority; an optional health gate skips a
source whose circuit is open; a source that raises or returns nothing falls
through to the next.  Every result carries its provenance and the capability
contract, so a consumer can never mistake a fallback for the primary.
"""

from __future__ import annotations

import hashlib
import json
import math
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from .catalog import CAPABILITIES, bindings_for
from .contracts import (
    RETIRED,
    UNSUPPORTED,
    Binding,
    Capability,
    CapabilityEvidence,
    CapabilityRequest,
    QualityReceipt,
)

Fetcher = Callable[..., Awaitable[Any]]
HealthGate = Callable[[str, str], Awaitable[bool]]


class CapabilityUnavailable(RuntimeError):
    """No bound source could serve the capability; ``attempts`` says why."""

    def __init__(self, capability: str, attempts: list[dict[str, Any]]) -> None:
        super().__init__(f"no source served {capability}: {attempts}")
        self.capability = capability
        self.attempts = attempts


@dataclass(frozen=True)
class CapabilityResult:
    capability: Capability
    source: str
    rows: Any
    binding: Binding
    attempts: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    request: CapabilityRequest | None = None
    quality: QualityReceipt | None = None

    @property
    def is_fallback(self) -> bool:
        return any(attempt["source"] != self.source for attempt in self.attempts)

    def provenance(self) -> dict[str, Any]:
        quality: dict[str, Any] | None = None
        if self.quality is not None:
            quality = {
                "status": self.quality.status,
                "row_count": self.quality.row_count,
                "coverage": self.quality.coverage,
                "effective_at_min": self.quality.effective_at_min.isoformat()
                if self.quality.effective_at_min else None,
                "effective_at_max": self.quality.effective_at_max.isoformat()
                if self.quality.effective_at_max else None,
                "available_at_min": self.quality.available_at_min.isoformat()
                if self.quality.available_at_min else None,
                "available_at_max": self.quality.available_at_max.isoformat()
                if self.quality.available_at_max else None,
                "response_hash": self.quality.response_hash,
                "schema": self.quality.schema,
                "warnings": list(self.quality.warnings),
            }
        return {"capability": self.capability.key, "source": self.source, "status": self.binding.status,
                "decision_eligible": self.binding.decision_eligible, "fallback": self.is_fallback,
                "purpose": self.request.purpose if self.request else "research", "quality": quality,
                "attempts": list(self.attempts)}


def _row_count(rows: Any) -> int | None:
    if isinstance(rows, tuple) and rows:
        rows = rows[0]
    try:
        return len(rows)
    except TypeError:
        return None


def _unpack_evidence(value: Any) -> tuple[Any, CapabilityEvidence]:
    if isinstance(value, CapabilityEvidence):
        return value.rows, value
    return value, CapabilityEvidence(rows=value)


def _response_hash(rows: Any) -> str | None:
    try:
        payload = json.dumps(rows, sort_keys=True, ensure_ascii=False, default=str, separators=(",", ":"))
    except (TypeError, ValueError):
        try:
            payload = repr(rows)
        except Exception:  # noqa: BLE001 - provenance must never break a source read
            return None
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _field_present(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, Decimal):
        return value.is_finite()
    return True


def _quality_receipt(rows: Any, envelope: CapabilityEvidence, request: CapabilityRequest) -> QualityReceipt:
    count = _row_count(rows)
    warnings = list(envelope.warnings)
    if envelope.coverage is not None and not 0 <= envelope.coverage <= 1:
        warnings.append("coverage_out_of_range")
    if request.required_fields and count:
        missing: set[str] = set()
        candidates = rows if isinstance(rows, (list, tuple)) else []
        for row in candidates:
            if not isinstance(row, dict):
                missing.update(request.required_fields)
                continue
            missing.update(field for field in request.required_fields if not _field_present(row.get(field)))
        if missing:
            warnings.append("missing_required_fields:" + ",".join(sorted(missing)))

    status = "complete"
    if count is None:
        status = "invalid"
        warnings.append("row_count_unavailable")
    elif count == 0:
        status = "empty"
    elif (
        request.required_fields and any(item.startswith("missing_required_fields:") for item in warnings)
    ) or (envelope.coverage is not None and not 0 <= envelope.coverage <= 1):
        status = "invalid"
    elif envelope.coverage is not None and envelope.coverage < 1:
        status = "partial"

    if request.max_age_seconds is not None:
        if request.as_of is None or envelope.available_at_max is None:
            status = "invalid"
            warnings.append("freshness_clock_missing")
        else:
            try:
                age = (request.as_of - envelope.available_at_max).total_seconds()
                if age > request.max_age_seconds:
                    status = "stale"
                    warnings.append(f"age_seconds={int(age)}")
            except TypeError:
                status = "invalid"
                warnings.append("incompatible_clock_timezone")

    return QualityReceipt(
        status=status,
        row_count=count,
        coverage=envelope.coverage,
        effective_at_min=envelope.effective_at_min,
        effective_at_max=envelope.effective_at_max,
        available_at_min=envelope.available_at_min,
        available_at_max=envelope.available_at_max,
        response_hash=_response_hash(rows),
        warnings=tuple(warnings),
    )


def _normalise_rows(rows: Any, binding: Binding) -> tuple[Any, bool, tuple[str, ...]]:
    """Project source-native dictionaries into the canonical field contract.

    Returns ``(rows, normalised, warnings)``. Only a binding with a spec is projected, and only when every
    row is a dictionary. Rows of any other shape (dataclasses such as ``Tick``, tuples) stay native and say
    so, so a receipt never calls them canonical. A unit factor applies to numbers only: multiplying the
    string ``"123"`` by an integer factor would silently repeat it.
    """
    spec = binding.spec
    if spec is None:
        return rows, False, ()
    if not isinstance(rows, (list, tuple)) or any(not isinstance(row, dict) for row in rows):
        return rows, False, ("rows_not_normalised",)
    warnings: list[str] = []
    normalised = []
    for row in rows:
        projected = dict(row)
        assigned: dict[str, Any] = {}
        for native, canonical in spec.field_map.items():
            if native not in row:
                continue
            if canonical in assigned and assigned[canonical] != row[native]:
                warnings.append(f"field_map_conflict:{canonical}")
                continue
            if canonical != native:
                projected.pop(native, None)
            projected[canonical] = assigned[canonical] = row[native]
        for field_name, factor in spec.unit_factors.items():
            value = projected.get(field_name)
            if value is None:
                continue
            if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
                warnings.append(f"unit_factor_skipped:{field_name}")
                continue
            if isinstance(value, Decimal) and isinstance(factor, float):
                factor = Decimal(str(factor))
            projected[field_name] = value * factor
        normalised.append(projected)
    return type(rows)(normalised), True, tuple(dict.fromkeys(warnings))


def _declared_fields(binding: Binding) -> set[str]:
    if binding.spec is None or not binding.spec.field_map:
        return set()
    return set(binding.spec.field_map.values())


class CapabilityResolver:
    def __init__(self, *, health_gate: HealthGate | None = None) -> None:
        self._fetchers: dict[tuple[str, str], Fetcher] = {}
        self._health_gate = health_gate

    def bind(self, source: str, capability: str, fetcher: Fetcher) -> None:
        """Attach an implementation; only catalogued, resolvable bindings are accepted."""
        if capability not in CAPABILITIES:
            raise ValueError(f"unknown capability {capability}")
        binding = next((item for item in bindings_for(capability, states=(
            "live_verified", "declared", "dormant", UNSUPPORTED, RETIRED)) if item.source == source), None)
        if binding is None:
            raise ValueError(f"{source} is not catalogued for {capability}")
        if binding.status in {UNSUPPORTED, RETIRED}:
            raise ValueError(f"{source} -> {capability} is {binding.status}; update the catalog first")
        self._fetchers[(source, capability)] = fetcher

    def bound_sources(self, capability: str) -> list[str]:
        return [item.source for item in bindings_for(capability) if (item.source, capability) in self._fetchers]

    def plan(self, capability: str) -> list[dict[str, Any]]:
        """Resolution order with whether each source has an implementation here."""
        return [{"source": item.source, "priority": item.priority, "status": item.status,
                 "bound": (item.source, capability) in self._fetchers} for item in bindings_for(capability)]

    async def fetch(self, capability: str, *, sources: Sequence[str] | None = None,
                    accept_empty: bool = False, request: CapabilityRequest | None = None,
                    **params: Any) -> CapabilityResult:
        """First source (in catalog order, optionally restricted) that answers."""
        if capability not in CAPABILITIES:
            raise ValueError(f"unknown capability {capability}")
        policy = request or CapabilityRequest(capability=capability, allow_empty=accept_empty)
        if policy.capability != capability:
            raise ValueError(f"request capability {policy.capability} does not match {capability}")
        allow_empty = accept_empty or policy.allow_empty
        allowed = set(sources) if sources is not None else None
        attempts: list[dict[str, Any]] = []
        for binding in bindings_for(capability):
            if allowed is not None and binding.source not in allowed:
                continue
            fetcher = self._fetchers.get((binding.source, capability))
            if fetcher is None:
                continue
            if policy.require_live_verified and binding.status != "live_verified":
                attempts.append({"source": binding.source, "status": "not_live_verified"})
                continue
            if policy.require_decision_eligible and not binding.decision_eligible:
                attempts.append({"source": binding.source, "status": "not_decision_eligible"})
                continue
            declared_fields = _declared_fields(binding)
            if policy.required_fields and declared_fields and not set(policy.required_fields).issubset(declared_fields):
                attempts.append({"source": binding.source, "status": "missing_required_fields",
                                 "missing": sorted(set(policy.required_fields) - declared_fields)})
                continue
            if self._health_gate is not None and not await self._health_gate(binding.source, capability):
                attempts.append({"source": binding.source, "status": "circuit_open"})
                continue
            started = time.monotonic()
            try:
                raw = await fetcher(**params)
            except Exception as error:  # noqa: BLE001 - fall through to the next source
                attempts.append({"source": binding.source, "status": "failed", "error": str(error)[:200],
                                 "ms": int((time.monotonic() - started) * 1000)})
                continue
            rows, envelope = _unpack_evidence(raw)
            rows, normalised, normalise_warnings = _normalise_rows(rows, binding)
            count = _row_count(rows)
            quality = _quality_receipt(rows, envelope, policy)
            quality = QualityReceipt(**{**quality.__dict__, "schema": "canonical" if normalised else "native",
                                        "warnings": quality.warnings + normalise_warnings})
            valid = quality.status not in {"invalid", "stale", "conflicted"}
            if policy.purpose in {"replay", "shadow"} and quality.status == "partial":
                valid = False
                quality = QualityReceipt(**{**quality.__dict__,
                                            "warnings": (*quality.warnings, f"purpose_requires_complete={policy.purpose}")})
            if count is None or (count < policy.min_rows and not (count == 0 and allow_empty)):
                valid = False
                if quality.status == "complete":
                    quality = QualityReceipt(**{**quality.__dict__, "status": "invalid",
                                                "warnings": (*quality.warnings, f"min_rows={policy.min_rows}")})
            if policy.min_coverage is not None and (quality.coverage is None or quality.coverage < policy.min_coverage):
                valid = False
                if quality.status not in {"invalid", "stale"}:
                    quality = QualityReceipt(**{**quality.__dict__, "status": "partial",
                                                "warnings": (*quality.warnings, f"min_coverage={policy.min_coverage}")})
            attempt_status = "completed" if quality.status == "complete" else quality.status
            attempts.append({"source": binding.source, "status": attempt_status, "rows": count,
                             "quality": quality.status, "response_hash": quality.response_hash,
                             "warnings": list(quality.warnings),
                             "ms": int((time.monotonic() - started) * 1000)})
            if count == 0 and not allow_empty:
                attempts[-1]["status"] = "empty"
                continue
            if not valid:
                continue
            return CapabilityResult(CAPABILITIES[capability], binding.source, rows, binding, tuple(attempts), policy, quality)
        raise CapabilityUnavailable(capability, attempts)


__all__ = ["CapabilityResolver", "CapabilityResult", "CapabilityUnavailable", "Fetcher", "HealthGate"]
