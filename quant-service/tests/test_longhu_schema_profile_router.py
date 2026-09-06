import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers.longhu_capabilities import build_longhu_capabilities_router


class LonghuSchemaProfileRouterTests(unittest.TestCase):
    def test_schema_profile_requires_read_key_and_keeps_research_boundary(self):
        async def call(_request):
            return {"pages": []}

        app = FastAPI()
        app.include_router(build_longhu_capabilities_router(
            configured=lambda: True, shared_read_key=lambda: "peer-key", call=call,
            schema_profile=lambda: {"status": "empty", "research_only": True, "live_effect": "none"},
        ))
        client = TestClient(app)
        self.assertEqual(client.get("/api/v1/research/longhu/schema-profile").status_code, 401)
        response = client.get(
            "/api/v1/research/longhu/schema-profile", headers={"X-Quant-Read-Key": "peer-key"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["live_effect"], "none")
