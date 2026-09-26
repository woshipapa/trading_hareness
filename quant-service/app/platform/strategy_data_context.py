"""Vendor-free strategy input plans and an injected capability read facade.

The strategy registry already records *what* a strategy needs.  This module
turns that declaration into a deterministic set of ``CapabilityRequest``
objects and provides a small, provider-agnostic read context.  It deliberately
does not know about PostgreSQL, HTTP clients or provider adapters: the caller
injects a loader (normally an adapter around ``CapabilityResolver`` or a
point-in-time repository).

Keeping the loader injected is important for research.  A strategy can use the
same plan with a live capability resolver, a persisted-evidence repository, or
a replay fixture without changing its rules or importing a vendor module.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from ..datasources.contracts import CapabilityRequest, QualityReceipt
from .strategy_data_needs import STRATEGY_DATA_NEEDS


CapabilityLoader = Callable[[CapabilityRequest], Awaitable[Any]]


@dataclass(frozen=True)
class StrategyCapabilityRead:
    """One strategy requirement compiled into a runtime read policy."""

    capability: str
    required: bool
    purpose: str
    taxonomies: tuple[str, ...]
    request: CapabilityRequest


@dataclass(frozen=True)
class StrategyDataPlan:
    """Immutable, vendor-free read plan for one strategy invocation."""

    strategy: str
    purpose: str
    as_of: Any | None
    reads: tuple[StrategyCapabilityRead, ...]

    @property
    def required_capabilities(self) -> tuple[str, ...]:
        return tuple(item.capability for item in self.reads if item.required)

    @property
    def optional_capabilities(self) -> tuple[str, ...]:
        return tuple(item.capability for item in self.reads if not item.required)


@dataclass(frozen=True)
class StrategyCapabilityValue:
    """A value returned by an injected loader, with optional evidence metadata."""

    value: Any
    quality: QualityReceipt | None = None
    provenance: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class StrategyDataContext:
    """Resolved strategy inputs; rules consume this instead of providers."""

    plan: StrategyDataPlan
    values: Mapping[str, Any]
    quality: Mapping[str, QualityReceipt | None]
    provenance: Mapping[str, Mapping[str, Any]]
    optional_missing: tuple[str, ...] = ()

    def value(self, capability: str, default: Any = None) -> Any:
        """Read one capability without exposing the loader implementation."""
        return self.values.get(capability, default)


class StrategyDataUnavailable(RuntimeError):
    """A required capability could not be resolved for a strategy invocation."""

    def __init__(self, strategy: str, capability: str, error: BaseException) -> None:
        self.strategy = strategy
        self.capability = capability
        self.cause = error
        super().__init__(f"required capability unavailable for {strategy}: {capability}")


def compile_strategy_data_plan(
    strategy: str,
    *,
    purpose: str = "research",
    as_of: Any | None = None,
    request_overrides: Mapping[str, Mapping[str, Any]] | None = None,
) -> StrategyDataPlan:
    """Compile the registry declaration into capability requests.

    ``request_overrides`` is intentionally keyed by capability rather than
    provider.  It lets a caller add a point-in-time field/freshness/coverage
    requirement while keeping source selection in the data-source layer.
    """

    try:
        needs = STRATEGY_DATA_NEEDS[strategy]
    except KeyError as error:
        raise ValueError(f"unknown strategy data needs: {strategy}") from error
    overrides = request_overrides or {}
    reads: list[StrategyCapabilityRead] = []
    for requirement in needs.needs:
        values = dict(overrides.get(requirement.capability, {}))
        values.setdefault("purpose", purpose)
        values.setdefault("as_of", as_of)
        values.setdefault("min_rows", 1 if requirement.required else 0)
        values.setdefault("allow_empty", not requirement.required)
        request = CapabilityRequest(requirement.capability, **values)
        reads.append(StrategyCapabilityRead(
            capability=requirement.capability,
            required=requirement.required,
            purpose=requirement.purpose,
            taxonomies=requirement.taxonomies,
            request=request,
        ))
    return StrategyDataPlan(strategy, purpose, as_of, tuple(reads))


def _unpack_loaded(value: Any) -> StrategyCapabilityValue:
    """Adapt ``CapabilityResult`` or plain repository rows without imports."""

    if isinstance(value, StrategyCapabilityValue):
        return value
    rows = getattr(value, "rows", value)
    quality = getattr(value, "quality", None)
    provenance_fn = getattr(value, "provenance", None)
    provenance = provenance_fn() if callable(provenance_fn) else {}
    return StrategyCapabilityValue(rows, quality, provenance)


def _quality_unusable(read: StrategyCapabilityRead, quality: QualityReceipt | None) -> str | None:
    """Return a stable reason when a resolved capability cannot feed a plan."""
    if quality is None:
        # Plain repository rows remain supported; those repositories enforce
        # their own point-in-time/readiness contract before returning.
        return None
    status = str(quality.status or "").strip().lower()
    if status in {"invalid", "stale", "conflicted", "empty"}:
        return f"quality:{status}"
    if read.request.purpose in {"replay", "shadow"} and status != "complete":
        return f"quality:{status or 'missing'}:purpose_requires_complete"
    return None


async def resolve_strategy_data(
    plan: StrategyDataPlan,
    loader: CapabilityLoader,
) -> StrategyDataContext:
    """Resolve a plan through an injected capability/repository loader.

    Optional reads are recorded as missing and do not stop the strategy.  A
    required read raises a stable, vendor-free error and never returns a
    partially populated context as if it were complete.
    """

    values: dict[str, Any] = {}
    quality: dict[str, QualityReceipt | None] = {}
    provenance: dict[str, Mapping[str, Any]] = {}
    optional_missing: list[str] = []
    for read in plan.reads:
        try:
            loaded = _unpack_loaded(await loader(read.request))
        except Exception as error:  # noqa: BLE001 - convert at the strategy boundary
            if read.required:
                raise StrategyDataUnavailable(plan.strategy, read.capability, error) from error
            optional_missing.append(read.capability)
            continue
        quality_error = _quality_unusable(read, loaded.quality)
        if quality_error is not None:
            error = ValueError(quality_error)
            if read.required:
                raise StrategyDataUnavailable(plan.strategy, read.capability, error) from error
            optional_missing.append(read.capability)
            continue
        values[read.capability] = loaded.value
        quality[read.capability] = loaded.quality
        provenance[read.capability] = dict(loaded.provenance)
    return StrategyDataContext(plan, values, quality, provenance, tuple(optional_missing))


async def resolve_strategy_data_from_resolver(
    resolver: Any,
    strategy: str,
    *,
    purpose: str = "research",
    as_of: Any | None = None,
    request_overrides: Mapping[str, Mapping[str, Any]] | None = None,
) -> StrategyDataContext:
    """Resolve a strategy plan through a data-source resolver instance.

    The import boundary points from the strategy facade to the resolver, not
    the other way around.  This keeps ``app.datasources`` infrastructure-only
    while allowing runtime composition to inject a live resolver, persisted
    repository adapter, or replay fixture.
    """
    plan = compile_strategy_data_plan(
        strategy, purpose=purpose, as_of=as_of, request_overrides=request_overrides,
    )

    async def load(request: CapabilityRequest) -> Any:
        return await resolver.fetch(request.capability, request=request)

    return await resolve_strategy_data(plan, load)


__all__ = [
    "CapabilityLoader", "StrategyCapabilityRead", "StrategyCapabilityValue", "StrategyDataContext",
    "StrategyDataPlan", "StrategyDataUnavailable", "compile_strategy_data_plan", "resolve_strategy_data",
    "resolve_strategy_data_from_resolver",
]
