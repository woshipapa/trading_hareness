from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.datasources import adapter_calls
from app.datasources.contracts import CapabilityEvidence
from app.routers.datasource_reads import build_datasource_reads_router


def client() -> TestClient:
    app = FastAPI()
    app.include_router(build_datasource_reads_router())
    return TestClient(app)


def test_read_route_parses_typed_query_and_marks_research_only(monkeypatch) -> None:
    async def fake(*, symbol: str, count: int) -> CapabilityEvidence:
        assert symbol == "600519.SH"
        assert count == 2
        return CapabilityEvidence(
            rows=[{"symbol": symbol, "at": datetime(2026, 10, 10, tzinfo=timezone.utc)}],
            coverage=1.0,
            warnings=("tdx_host=test",),
        )

    from app.datasources.sources import tdx_bars
    monkeypatch.setattr(tdx_bars, "fetch_daily", fake)
    adapter_calls._LAST_READ.clear()
    response = client().get("/api/v1/datasources/read/tdx_public/bars.daily", params={"symbol": "600519.SH", "count": "2"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["decision_eligible"] is False
    assert payload["rows"][0]["at"].endswith("+00:00")
    assert payload["warnings"] == ["tdx_host=test"]


def test_read_route_rejects_unknown_and_missing_query() -> None:
    response = client().get("/api/v1/datasources/read/tdx_public/bars.daily", params={"symbol": "600519.SH", "extra": "1"})
    assert response.status_code == 422
    response = client().get("/api/v1/datasources/read/tdx_public/bars.daily", params={"count": "2"})
    assert response.status_code == 422


def test_read_route_rejects_unsupported_binding_and_rate_limits(monkeypatch) -> None:
    assert client().get("/api/v1/datasources/read/tushare_primary/bars.daily").status_code == 404
    monkeypatch.setattr(adapter_calls, "research_reads_enabled", lambda: False)
    assert client().get("/api/v1/datasources/read/tdx_public/bars.daily").status_code == 503
    monkeypatch.setattr(adapter_calls, "research_reads_enabled", lambda: True)

    async def fake(*, symbol: str, count: int) -> CapabilityEvidence:
        return CapabilityEvidence(rows=[])

    from app.datasources.sources import tdx_bars
    monkeypatch.setattr(tdx_bars, "fetch_daily", fake)
    adapter_calls._LAST_READ.clear()
    first = client().get("/api/v1/datasources/read/tdx_public/bars.daily", params={"symbol": "600519.SH", "count": "1"})
    second = client().get("/api/v1/datasources/read/tdx_public/bars.daily", params={"symbol": "600519.SH", "count": "1"})
    assert first.status_code == 200
    assert second.status_code == 429
