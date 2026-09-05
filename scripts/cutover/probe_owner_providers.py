"""Read-only provider smoke probe for the owner runtime.

The probe makes one bounded request per route and prints status/count only;
credentials and response payloads are never emitted.
"""

from __future__ import annotations

import asyncio
import sys

sys.path.insert(0, "/app")

from app.tushare_providers import call_provider, provider_configs


async def main() -> None:
    configs = provider_configs()
    print("providers", [(name, provider.configured, provider.protocol, provider.get_gateway_mode)
                        for name, provider in configs.items()])
    for name in ("super_get", "super_sdk"):
        provider = configs[name]
        for api_name, params in (
            ("daily", {"ts_code": "000001.SZ", "start_date": "20260901"}),
            ("rt_k", {"ts_code": "000636.SZ"}),
            ("rt_min", {"ts_code": "000636.SZ"}),
        ):
            if not provider.configured or not provider.supports(api_name):
                print(name, api_name, "SKIP")
                continue
            try:
                rows = await call_provider(provider, api_name, params, None)
                print(name, api_name, "OK", len(rows))
            except Exception as exc:  # noqa: BLE001 - smoke probe reports route failure
                print(name, api_name, "ERROR", type(exc).__name__, str(exc)[:160])


if __name__ == "__main__":
    asyncio.run(main())
