import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.datasources.catalog import BINDINGS, TAXONOMIES
from app.routers.datasource_catalog import build_datasource_catalog_router


def client() -> TestClient:
    app = FastAPI()
    app.include_router(build_datasource_catalog_router())
    return TestClient(app)


class DatasourceCatalogRouterTests(unittest.TestCase):
    def test_a_source_filter_returns_every_binding_and_taxonomy_of_that_source(self):
        response = client().get("/api/v1/datasources/catalog", params={"source": "tdx_mac"})
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual({(binding["source"], item["key"]) for item in payload["capabilities"] for binding in item["bindings"]},
                         {(item.source, item.capability) for item in BINDINGS if item.source == "tdx_mac"})
        self.assertEqual({item["key"] for item in payload["taxonomies"]},
                         {item.key for item in TAXONOMIES.values() if item.source == "tdx_mac"})

    def test_a_status_filter_keeps_only_bindings_of_that_status(self):
        status = next(item.status for item in BINDINGS if item.source == "tdx_public")
        payload = client().get("/api/v1/datasources/catalog", params={"source": "tdx_public", "status": status}).json()
        self.assertTrue(payload["capabilities"])
        self.assertEqual({binding["status"] for item in payload["capabilities"] for binding in item["bindings"]}, {status})

    def test_a_capability_lists_all_its_bindings_with_every_spec_field(self):
        payload = client().get("/api/v1/datasources/capabilities/bars.minute").json()
        self.assertEqual({(item["source"], item["status"]) for item in payload["bindings"]},
                         {(item.source, item.status) for item in BINDINGS if item.capability == "bars.minute"})
        mac = next(item for item in payload["bindings"] if item["source"] == "tdx_mac")
        spec = next(item.spec for item in BINDINGS if item.source == "tdx_mac" and item.capability == "bars.minute")
        self.assertEqual(set(mac["spec"]["agreement"]), set(spec.agreement), "the agreement entries reach the API")
        self.assertIsInstance(payload["evidence_locations"], list)
        self.assertIn("schema", payload)

    def test_an_unknown_capability_is_404(self):
        self.assertEqual(client().get("/api/v1/datasources/capabilities/no.such.capability").status_code, 404)


if __name__ == "__main__":
    unittest.main()
