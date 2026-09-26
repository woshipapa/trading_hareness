"""Fail-closed readiness projections for optional research frameworks.

The database catalog describes an integration contract; it does not prove that
an external framework has been installed, supplied with point-in-time data, or
run successfully.  This module keeps those facts separate and deliberately
does not import heavy ML/backtest packages or execute a benchmark.
"""

from __future__ import annotations

import importlib.util
from collections.abc import Callable, Iterable, Mapping
from typing import Any


# These are probes only.  They must never be treated as a benchmark result.
_OPTIONAL_PACKAGES: dict[str, tuple[str, ...]] = {
    "alphalens": ("alphalens",),
    "qlib": ("qlib",),
    "lean": ("quantconnect", "lean"),
    "finrl": ("finrl",),
    "vectorbt": ("vectorbt",),
}

_DATA_GATES: dict[str, tuple[str, ...]] = {
    "alphalens": ("factor_export_and_forward_returns", "sufficient_cross_section"),
    "qlib": ("point_in_time_history_3y", "delisting_aware_universe", "qlib_dataset_export"),
    "lean": ("a_share_transaction_model", "broker_data_bridge"),
    "finrl": ("a_share_gym_environment", "point_in_time_history_3y", "gpu_worker"),
    "vectorbt": ("isolated_research_environment", "license_review"),
}


def _default_probe(package: str) -> bool:
    """Return whether an optional package can be discovered, without importing it."""
    try:
        return importlib.util.find_spec(package) is not None
    except (ImportError, ModuleNotFoundError, ValueError):
        # A broken namespace/package installation is unavailable, not ready.
        return False


def _row_value(row: Mapping[str, Any], key: str, default: Any = None) -> Any:
    value = row.get(key, default)
    return default if value is None else value


def _dependency_state(
    framework_key: str,
    package_probe: Callable[[str], bool],
) -> dict[str, Any]:
    packages = _OPTIONAL_PACKAGES.get(framework_key, ())
    if not packages:
        return {"state": "not_required", "packages": []}
    installed = [package for package in packages if package_probe(package)]
    return {
        "state": "available" if installed else "missing",
        "packages": list(packages),
        "available_packages": installed,
    }


def framework_readiness(
    row: Mapping[str, Any],
    *,
    package_probe: Callable[[str], bool] | None = None,
) -> dict[str, Any]:
    """Return an auditable, non-promoting readiness projection for one row.

    ``adapter_ready`` means only that this repository has described an adapter
    contract.  It intentionally remains blocked until the external benchmark,
    data gates, and reproducible artifact checks have evidence.
    """
    key = str(_row_value(row, "framework_key", ""))
    catalog_status = str(_row_value(row, "status", "unknown"))
    probe = package_probe or _default_probe
    dependency = _dependency_state(key, probe)
    data_gates = list(_DATA_GATES.get(key, ()))
    data_gate_status = {gate: "not_evaluated" for gate in data_gates}

    if catalog_status == "native":
        readiness_status = "available"
        benchmark_status = "native_baseline_only"
        blockers: list[str] = []
    elif catalog_status == "planned":
        readiness_status = "planned"
        benchmark_status = "not_executed"
        blockers = ["adapter_not_implemented"]
    elif catalog_status == "adapter_ready":
        readiness_status = "blocked"
        benchmark_status = "not_executed"
        blockers = ["cross_framework_benchmark_not_executed", "data_gates_not_evaluated"]
        if dependency["state"] == "missing":
            blockers.append("optional_dependency_unavailable")
    else:
        readiness_status = "unknown"
        benchmark_status = "not_executed"
        blockers = ["catalog_status_unrecognized"]

    return {
        "status": readiness_status,
        "catalog_status": catalog_status,
        "benchmark_status": benchmark_status,
        "runtime_dependency": dependency,
        "required_data_gates": data_gates,
        "data_gate_status": data_gate_status,
        "blocked_reasons": blockers,
        "research_only": True,
        "live_effect": "none",
    }


def enrich_framework_rows(
    rows: Iterable[Mapping[str, Any]],
    *,
    package_probe: Callable[[str], bool] | None = None,
) -> list[dict[str, Any]]:
    """Attach readiness to catalog rows while preserving the catalog contract."""
    return [
        {**dict(row), "readiness": framework_readiness(row, package_probe=package_probe)}
        for row in rows
    ]


__all__ = ["enrich_framework_rows", "framework_readiness"]
