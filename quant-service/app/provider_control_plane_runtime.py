"""Startup runtime for local provider capability and limiter projections.

It materializes declared catalog contracts and the process-effective limiter
configuration, but never contacts a provider or stores credentials.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import json
from typing import Any

from .tushare_providers import RETIRED_PROVIDER_KEYS

#: Both the main service and the scheduler project this identical catalog at
#: startup. On 2026-10-08 they wrote the same ~250 rows one round trip at a time
#: over a slow tunnel; one held the row locks for tens of seconds and the other
#: failed its 30-second startup check. Whoever takes this transaction-scoped lock
#: projects; the other skips, because it would write exactly the same rows.
CATALOG_PROJECTION_LOCK = 0x7163_6174  # "qcat"

_DECLARE_CAPABILITIES = """
    INSERT INTO quant.provider_api_capabilities(provider_key,api_name,availability,frequency,decision_eligible,note,metadata)
    SELECT d.provider_key,d.api_name,'declared',d.frequency,d.decision_eligible,d.note,d.metadata::jsonb
      FROM unnest(%s::text[],%s::text[],%s::text[],%s::boolean[],%s::text[],%s::text[])
           AS d(provider_key,api_name,frequency,decision_eligible,note,metadata)
     ORDER BY d.provider_key,d.api_name
    ON CONFLICT(provider_key,api_name) DO UPDATE SET frequency=EXCLUDED.frequency,
      decision_eligible=EXCLUDED.decision_eligible,
      metadata=quant.provider_api_capabilities.metadata || EXCLUDED.metadata
"""


def mirror_runtime_rate_limits(connection: Any, configs: Mapping[str, Any]) -> None:
    """Mirror effective local rate limits into the read-only control plane."""
    for provider in sorted(configs.values(), key=lambda item: str(item.key)):
        if str(provider.key) in RETIRED_PROVIDER_KEYS:
            continue
        rate_limit = int(provider.rate_limit_per_minute)
        provider_key = str(provider.key)
        connection.execute(
            """UPDATE quant.provider_capabilities SET rate_limit_per_minute=%s
                 WHERE provider_key=%s AND market='cn'""",
            (rate_limit, provider_key),
        )
        connection.execute(
            """UPDATE quant.providers
                  SET config=config || jsonb_build_object(
                        'rate_limit_source','runtime_environment',
                        'runtime_rate_limit_per_minute',%s
                      ),updated_at=now()
                 WHERE provider_key=%s""",
            (rate_limit, provider_key),
        )


@dataclass(frozen=True)
class ProviderControlPlaneRuntimeDependencies:
    database: Any
    provider_configs: Callable[[], Mapping[str, Any]]
    catalog_items: Callable[[], list[dict[str, Any]]]
    capability_contract: Callable[[str], Any]
    super_get_verified_apis: frozenset[str]


class ProviderControlPlaneRuntime:
    """Project declared capabilities and active local limits in one short transaction."""

    def __init__(self, dependencies: ProviderControlPlaneRuntimeDependencies) -> None:
        self._dependencies = dependencies

    def declarations(self) -> list[tuple[str, str, str, bool, str, str]]:
        """``(provider_key, api_name, frequency, decision_eligible, note, metadata_json)``, sorted and unique."""
        dependencies = self._dependencies
        rows: dict[tuple[str, str], tuple[str, str, str, bool, str, str]] = {}
        for item in dependencies.catalog_items():
            api_name = str(item["api_name"])
            contract = dependencies.capability_contract(api_name)
            provider_keys = ["tushare_super_sdk"]
            if api_name in dependencies.super_get_verified_apis:
                provider_keys.append("tushare_super_get")
            if api_name == "stock_basic":
                provider_keys.append("tushare_backup")
            metadata = json.dumps({
                "catalog_origin": item["catalog_origin"],
                "permission_model": item["permission_model"],
                "min_points": item["min_points"],
                "request_policy": item["request_policy"],
                "model_role": item["model_role"],
                "priority": item["priority"],
            }, ensure_ascii=False, sort_keys=True, default=str)
            for provider_key in provider_keys:
                rows[(provider_key, api_name)] = (
                    provider_key, api_name, str(contract.frequency), bool(contract.decision_eligible),
                    str(contract.note)[:500], metadata,
                )
        return [rows[key] for key in sorted(rows)]

    def initialize(self) -> bool:
        """Project the catalog in one statement; ``False`` when another process holds the lock."""
        dependencies = self._dependencies
        declarations = self.declarations()
        configs = dependencies.provider_configs()
        with dependencies.database.transaction() as connection:
            acquired = connection.execute(
                "SELECT pg_try_advisory_xact_lock(%s) AS acquired", (CATALOG_PROJECTION_LOCK,),
            ).fetchone()
            if not (acquired["acquired"] if isinstance(acquired, Mapping) else acquired[0]):
                return False
            mirror_runtime_rate_limits(connection, configs)
            if declarations:
                connection.execute(_DECLARE_CAPABILITIES, tuple(list(column) for column in zip(*declarations)))
        return True


__all__ = [
    "CATALOG_PROJECTION_LOCK",
    "ProviderControlPlaneRuntime",
    "ProviderControlPlaneRuntimeDependencies",
    "mirror_runtime_rate_limits",
]
