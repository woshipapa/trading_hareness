"""Bounded invocation of catalog adapters exposed for research reads."""

from __future__ import annotations

import asyncio
import importlib
import inspect
import os
import time
from collections.abc import Sequence
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, get_origin, get_type_hints

from . import catalog
from .contracts import CapabilityEvidence
from .sources.tdx_protocol import TdxProtocolError

_READ_SEMAPHORE = asyncio.Semaphore(2)
_RATE_LOCK = asyncio.Lock()
_LAST_READ: dict[tuple[str, str], float] = {}


class AdapterCallError(Exception):
    """A boundary error with an HTTP-compatible status and detail."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def research_reads_enabled() -> bool:
    return os.getenv("DATASOURCE_RESEARCH_READ_ENABLED", "true").lower() not in {"0", "false", "no", "off"}


def adapter_function(adapter: str) -> Any:
    """The function an ``app/<module path>.py:<function>`` adapter string names."""
    path, _, name = adapter.partition(":")
    return getattr(importlib.import_module(path.removesuffix(".py").replace("/", ".")), name, None)


def _research_readable(adapter: str | None) -> bool:
    """Only the TDX package adapters answer research reads; licensed and vendor sources have their own routes."""
    if not adapter or ":" not in adapter:
        return False
    path = Path(adapter.partition(":")[0])
    return (path.parent.name == "sources" and path.name.startswith("tdx_") and path.suffix == ".py") or \
        path.as_posix() == "app/datasources/derived/limit_pools.py"


def _research_function(binding: Any) -> tuple[Any, str]:
    """The adapter a research read of ``binding`` calls, or None and the reason there is none."""
    if not _research_readable(binding.adapter):
        return None, "this binding is served by a licensed or vendor route"
    function = adapter_function(binding.adapter)
    if not callable(function):
        return None, "catalog adapter is not callable"
    if any(parameter.kind is not inspect.Parameter.KEYWORD_ONLY for parameter in inspect.signature(function).parameters.values()):
        return None, "catalog adapter does not expose a keyword-only research signature"
    return function, ""


def research_readable(binding: Any) -> bool:
    """Whether GET /api/v1/datasources/read serves ``binding``: the catalog says so to the console."""
    return _research_function(binding)[0] is not None


def resolve_adapter(source: str, capability: str) -> tuple[Any, Any]:
    bindings = [item for item in catalog.BINDINGS if item.source == source and item.capability == capability]
    if not bindings:
        raise AdapterCallError(404, "source/capability binding not found")
    function, reason = _research_function(bindings[0])
    if function is None:
        raise AdapterCallError(404, reason)
    return bindings[0], function


def _parse_value(name: str, value: str, annotation: Any) -> Any:
    if get_origin(annotation) in {Sequence, list, tuple}:
        return [part for part in value.split(",") if part]
    if annotation is int:
        return int(value)
    if annotation is date:
        return date.fromisoformat(value)
    if annotation is str or annotation is Any or annotation is inspect.Parameter.empty:
        return value
    raise AdapterCallError(422, f"unsupported query parameter type for {name}")


def parse_query(function: Any, query: dict[str, list[str]]) -> dict[str, Any]:
    signature = inspect.signature(function)
    hints = get_type_hints(function)
    unknown = sorted(set(query) - set(signature.parameters))
    if unknown:
        raise AdapterCallError(422, f"unknown query parameter: {unknown[0]}")
    result: dict[str, Any] = {}
    for name, parameter in signature.parameters.items():
        values = query.get(name, [])
        if not values:
            if parameter.default is inspect.Parameter.empty:
                raise AdapterCallError(422, f"missing query parameter: {name}")
            result[name] = parameter.default
            continue
        annotation = hints.get(name, parameter.annotation)
        if get_origin(annotation) in {Sequence, list, tuple}:
            value = ",".join(values)
        elif len(values) == 1:
            value = values[0]
        else:
            raise AdapterCallError(422, f"query parameter repeated: {name}")
        try:
            result[name] = _parse_value(name, value, annotation)
        except (TypeError, ValueError) as error:
            raise AdapterCallError(422, f"invalid query parameter {name}: {error}") from error
    count = result.get("count")
    if count is not None and count > 800:
        raise AdapterCallError(422, "count must be at most 800")
    symbols = result.get("symbols")
    if symbols is not None and len(symbols) > 80:
        raise AdapterCallError(422, "symbols must contain at most 80 values")
    return result


def _json_safe(value: Any) -> Any:
    if isinstance(value, (date, Decimal)):
        return value.isoformat() if isinstance(value, date) else float(value)
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


async def read_adapter(source: str, capability: str, query: dict[str, list[str]]) -> dict[str, Any]:
    if not research_reads_enabled():
        raise AdapterCallError(503, "datasource research reads are disabled")
    binding, function = resolve_adapter(source, capability)
    params = parse_query(function, query)
    key = (source, capability)
    now = time.monotonic()
    async with _RATE_LOCK:
        previous = _LAST_READ.get(key)
        if previous is not None and now - previous < 1.0:
            raise AdapterCallError(429, "one research read per source and capability per second")
        _LAST_READ[key] = now
    started = datetime.now(timezone.utc)
    async with _READ_SEMAPHORE:
        try:
            result = function(**params)
            evidence = await result if inspect.isawaitable(result) else result
        # The HTTP boundary: an adapter refusing the request (a non-index symbol, limit rows of another session) is the
        # caller's 422, an upstream that did not answer is a 502; both keep the adapter's reason.
        except ValueError as error:
            raise AdapterCallError(422, f"{type(error).__name__}: {error}") from error
        except (OSError, TdxProtocolError) as error:
            raise AdapterCallError(502, f"{type(error).__name__}: {error}") from error
    finished = datetime.now(timezone.utc)
    if isinstance(evidence, CapabilityEvidence):
        rows = list(evidence.rows)
        payload = {
            "coverage": evidence.coverage,
            "effective_at_min": evidence.effective_at_min,
            "effective_at_max": evidence.effective_at_max,
            "available_at_min": evidence.available_at_min,
            "available_at_max": evidence.available_at_max,
            "warnings": list(evidence.warnings),
        }
    else:
        rows = list(evidence)
        payload = {"coverage": None, "effective_at_min": None, "effective_at_max": None,
                   "available_at_min": None, "available_at_max": None, "warnings": []}
    truncated = len(rows) > 2000
    return {
        "source": source,
        "capability": capability,
        "status": binding.status,
        "decision_eligible": False,
        "decision_eligible_reason": "research reads are evidence-only and never decision eligible",
        "params": _json_safe(params),
        "started_at": started,
        "finished_at": finished,
        **_json_safe(payload),
        "rows": _json_safe(rows[:2000]),
        "truncated": truncated,
    }


__all__ = ["AdapterCallError", "parse_query", "read_adapter", "resolve_adapter"]
