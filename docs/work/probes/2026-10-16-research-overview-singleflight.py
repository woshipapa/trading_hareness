"""Stress the current-source research overview ASGI route.

The route is read-only.  This probe runs against a disposable compose process
with the working tree mounted as the source overlay, so it measures the code
that a code-only release would execute rather than the image's baked source.
"""

from __future__ import annotations

import asyncio
import json
import time

import httpx

from app.main import app, async_db


PATH = "/api/v1/research/overview"


async def run_level(client: httpx.AsyncClient, concurrency: int) -> dict[str, object]:
    async def one() -> dict[str, object]:
        started = time.perf_counter()
        try:
            response = await client.get(PATH, timeout=90)
            return {"status": response.status_code, "ms": (time.perf_counter() - started) * 1000}
        except Exception as error:  # pragma: no cover - exercised by a live probe
            return {"status": type(error).__name__, "ms": (time.perf_counter() - started) * 1000}

    results = await asyncio.gather(*(one() for _ in range(concurrency)))
    times = sorted(float(item["ms"]) for item in results)
    p95_index = min(len(times) - 1, round((len(times) - 1) * 0.95))
    return {
        "concurrency": concurrency,
        "statuses": {
            str(status): sum(1 for item in results if item["status"] == status)
            for status in sorted({item["status"] for item in results}, key=str)
        },
        "p50_ms": round(times[len(times) // 2], 1),
        "p95_ms": round(times[p95_index], 1),
        "max_ms": round(max(times), 1),
    }


async def main() -> None:
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://overlay") as client:
        results = [await run_level(client, concurrency) for concurrency in (1, 2, 4, 8)]
    pool = async_db.pool_status()
    await async_db.close()
    print(json.dumps({"path": PATH, "pool_after_matrix": pool, "results": results}))


if __name__ == "__main__":
    asyncio.run(main())
