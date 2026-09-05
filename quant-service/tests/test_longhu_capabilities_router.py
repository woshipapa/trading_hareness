from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers.longhu_capabilities import build_longhu_capabilities_router


def _client(enabled: bool = True) -> tuple[TestClient, list[dict]]:
    calls: list[dict] = []

    async def call(request: dict):
        calls.append(request)
        return {"target": request["target"], "calls": 1, "pages": [{"payload": {"errcode": 0, "rows": [1]}}]}

    app = FastAPI()
    app.include_router(build_longhu_capabilities_router(
        configured=lambda: enabled,
        shared_read_key=lambda: "peer-key",
        call=call,
    ))
    return TestClient(app), calls


def test_catalog_and_probe_require_shared_key_and_mark_research_only():
    http, calls = _client()
    assert http.get("/api/v1/research/longhu/catalog").status_code == 401
    catalog = http.get(
        "/api/v1/research/longhu/catalog", headers={"X-Quant-Read-Key": "peer-key"},
    )
    assert catalog.status_code == 200
    assert catalog.json()["research_only"] is True
    response = http.post(
        "/api/v1/research/longhu/probe",
        headers={"X-Quant-Read-Key": "peer-key"},
        json={"target": "longhu_quote", "params": {"a": "GetStockBid", "Token": "redact"}},
    )
    assert response.status_code == 200
    assert calls[0]["params"]["a"] == "GetStockBid"
    assert "Token" not in response.json()["request"]["params"]
    assert response.json()["observation"]["live_effect"] == "none"


def test_disabled_provider_returns_503():
    http, _ = _client(enabled=False)
    response = http.get(
        "/api/v1/research/longhu/catalog", headers={"X-Quant-Read-Key": "peer-key"},
    )
    assert response.status_code == 503
