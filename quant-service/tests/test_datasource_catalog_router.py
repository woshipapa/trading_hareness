from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers.datasource_catalog import build_datasource_catalog_router


def client() -> TestClient:
    app = FastAPI()
    app.include_router(build_datasource_catalog_router())
    return TestClient(app)


def test_catalog_includes_taxonomies_and_filters_bindings() -> None:
    response = client().get("/api/v1/datasources/catalog", params={"source": "tdx_mac", "status": "unsupported"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["taxonomies"]
    assert all(item["source"] == "tdx_mac" for item in payload["taxonomies"])
    assert payload["capabilities"]
    assert all(binding["source"] == "tdx_mac" and binding["status"] == "unsupported"
               for item in payload["capabilities"] for binding in item["bindings"])


def test_capability_returns_all_bindings_and_evidence_locations() -> None:
    response = client().get("/api/v1/datasources/capabilities/bars.minute")
    assert response.status_code == 200
    payload = response.json()
    assert {item["status"] for item in payload["bindings"]} >= {"declared", "unsupported"}
    assert isinstance(payload["evidence_locations"], list)


def test_unknown_capability_is_404() -> None:
    assert client().get("/api/v1/datasources/capabilities/no.such.capability").status_code == 404
