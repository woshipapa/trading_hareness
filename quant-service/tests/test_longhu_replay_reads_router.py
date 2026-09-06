from fastapi import FastAPI
from fastapi.testclient import TestClient
import unittest

from app.routers.longhu_replay_reads import build_longhu_replay_reads_router


class LonghuReplayReadsRouterTests(unittest.TestCase):
    def test_longhu_replay_readiness_is_a_read_only_background_projection(self):
        app = FastAPI()
        app.include_router(build_longhu_replay_reads_router(lambda: {
            "status": "accumulating", "research_only": True, "live_effect": "none",
        }))
        response = TestClient(app).get("/api/v1/research/longhu/replay-readiness")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["live_effect"], "none")
