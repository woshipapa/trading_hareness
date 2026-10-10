import unittest
from datetime import datetime, timezone
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.datasources import adapter_calls
from app.datasources.contracts import CapabilityEvidence
from app.datasources.sources import tdx_bars
from app.routers.datasource_reads import build_datasource_reads_router

READ = "/api/v1/datasources/read/tdx_public/bars.daily"


def client() -> TestClient:
    app = FastAPI()
    app.include_router(build_datasource_reads_router())
    return TestClient(app)


class DatasourceReadRouterTests(unittest.TestCase):
    def setUp(self):
        adapter_calls._LAST_READ.clear()

    def test_a_read_parses_the_typed_query_and_is_marked_research_only(self):
        async def fake(*, symbol: str, count: int) -> CapabilityEvidence:
            self.assertEqual((symbol, count), ("600519.SH", 2))
            return CapabilityEvidence(rows=[{"symbol": symbol, "at": datetime(2026, 10, 10, tzinfo=timezone.utc)}],
                                      coverage=1.0, warnings=("tdx_host=test",))

        with mock.patch.object(tdx_bars, "fetch_daily", fake):
            response = client().get(READ, params={"symbol": "600519.SH", "count": "2"})
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIs(payload["decision_eligible"], False)
        self.assertTrue(payload["rows"][0]["at"].endswith("+00:00"))
        self.assertEqual(payload["warnings"], ["tdx_host=test"])

    def test_an_unknown_or_missing_query_parameter_is_422(self):
        self.assertEqual(client().get(READ, params={"symbol": "600519.SH", "extra": "1"}).status_code, 422)
        self.assertEqual(client().get(READ, params={"count": "2"}).status_code, 422)
        self.assertEqual(client().get(READ, params={"symbol": "600519.SH", "count": "801"}).status_code, 422)

    def test_a_vendor_binding_is_404_the_switch_is_503_and_a_second_read_within_a_second_is_429(self):
        self.assertEqual(client().get("/api/v1/datasources/read/tushare_primary/bars.daily").status_code, 404)
        with mock.patch.object(adapter_calls, "research_reads_enabled", lambda: False):
            self.assertEqual(client().get(READ).status_code, 503)

        async def fake(*, symbol: str, count: int) -> CapabilityEvidence:
            return CapabilityEvidence(rows=[])

        with mock.patch.object(tdx_bars, "fetch_daily", fake):
            first = client().get(READ, params={"symbol": "600519.SH", "count": "1"})
            second = client().get(READ, params={"symbol": "600519.SH", "count": "1"})
        self.assertEqual((first.status_code, second.status_code), (200, 429))


if __name__ == "__main__":
    unittest.main()
