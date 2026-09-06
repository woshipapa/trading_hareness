from datetime import date

from app.longhu_supplemental_service import SUPPLEMENTAL_REQUESTS, sync


def test_supplemental_sync_persists_each_page_and_degrades_per_capability():
    calls = []
    stored = []

    class Source:
        def raw_call(self, request):
            calls.append(request)
            if request["params"]["a"] == "MoodNumCount":
                raise RuntimeError("provider unavailable")
            return {"pages": [{"payload": {"errcode": 0, "list": [["600000", 1]]}}]}

    async def run_public(fn, request, **_kwargs):
        return fn(request)

    async def persist(provider, capability, rows):
        stored.append((provider, capability, rows))
        return len(rows)

    import asyncio
    result = asyncio.run(sync(date(2026, 9, 4), run_public_blocking=run_public, persist=persist, source_factory=Source))
    assert len(calls) == len(SUPPLEMENTAL_REQUESTS)
    assert result["status"] == "partial"
    assert result["capabilities"]["MoodNumCount"]["status"] == "failed"
    assert result["stored"] == len(SUPPLEMENTAL_REQUESTS) - 1
