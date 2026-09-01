import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers.l2_research import L2ResearchDependencies, build_l2_research_router


class L2ResearchRouterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.recorded = []

        async def record(payload):
            self.recorded.append(payload)
            return {"status": "blocked", "samples": len(payload.rows), "live_effect": "none"}

        async def latest():
            return {"status": "blocked", "reason": "no_persisted_licensed_level2_evaluation", "live_effect": "none"}

        app = FastAPI()
        app.include_router(build_l2_research_router(L2ResearchDependencies(record=record, latest=latest)))
        self.client = TestClient(app)

    def test_latest_is_explicitly_blocked_without_evidence(self):
        response = self.client.get("/api/v1/research/l2/evaluations/latest")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "blocked")

    def test_record_route_validates_and_delegates(self):
        response = self.client.post("/api/v1/research/l2/evaluations", json={
            "rows": [{"baseline_score": 0.1, "l2_score": 0.2, "outcome": 0.3, "l2_algorithm_version": "licensed-v1"}],
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["samples"], 1)
        self.assertEqual(len(self.recorded), 1)

    def test_provider_proxy_source_is_not_accepted(self):
        response = self.client.post("/api/v1/research/l2/evaluations", json={
            "source_kind": "tencent_order_book",
            "rows": [{"baseline_score": 0.1, "l2_score": 0.2, "outcome": 0.3, "l2_algorithm_version": "proxy-v1"}],
        })
        self.assertEqual(response.status_code, 422)


if __name__ == "__main__":
    unittest.main()
