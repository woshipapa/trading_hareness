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

import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from .catalog import CAPABILITIES, bindings_for
from .contracts import RETIRED, UNSUPPORTED, Binding, Capability


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

    @property
    def is_fallback(self) -> bool:
        return any(attempt["source"] != self.source for attempt in self.attempts)

    def provenance(self) -> dict[str, Any]:
        return {"capability": self.capability.key, "source": self.source, "status": self.binding.status,
                "decision_eligible": self.binding.decision_eligible, "fallback": self.is_fallback,
                "attempts": list(self.attempts)}


def _row_count(rows: Any) -> int | None:
    if isinstance(rows, tuple) and rows:
        rows = rows[0]
    try:
        return len(rows)
    except TypeError:
        return None


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
                    accept_empty: bool = False, **params: Any) -> CapabilityResult:
        """First source (in catalog order, optionally restricted) that answers."""
        if capability not in CAPABILITIES:
            raise ValueError(f"unknown capability {capability}")
        allowed = set(sources) if sources is not None else None
        attempts: list[dict[str, Any]] = []
        for binding in bindings_for(capability):
            if allowed is not None and binding.source not in allowed:
                continue
            fetcher = self._fetchers.get((binding.source, capability))
            if fetcher is None:
                continue
            if self._health_gate is not None and not await self._health_gate(binding.source, capability):
                attempts.append({"source": binding.source, "status": "circuit_open"})
                continue
            started = time.monotonic()
            try:
                rows = await fetcher(**params)
            except Exception as error:  # noqa: BLE001 - fall through to the next source
                attempts.append({"source": binding.source, "status": "failed", "error": str(error)[:200],
                                 "ms": int((time.monotonic() - started) * 1000)})
                continue
            count = _row_count(rows)
            attempts.append({"source": binding.source, "status": "completed", "rows": count,
                             "ms": int((time.monotonic() - started) * 1000)})
            if count == 0 and not accept_empty:
                attempts[-1]["status"] = "empty"
                continue
            return CapabilityResult(CAPABILITIES[capability], binding.source, rows, binding, tuple(attempts))
        raise CapabilityUnavailable(capability, attempts)


__all__ = ["CapabilityResolver", "CapabilityResult", "CapabilityUnavailable", "Fetcher", "HealthGate"]
