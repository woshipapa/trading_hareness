import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers.xiaojie_leader_flow import build_xiaojie_leader_flow_router
from app.xiaojie_leader_flow import evaluate_snapshot


class XiaojieLeaderFlowRouterTests(unittest.TestCase):
    def test_evaluate_route_is_research_only_and_typed(self):
        app = FastAPI()
        app.include_router(build_xiaojie_leader_flow_router(evaluate_snapshot))
        payload = {
            "snapshot": {
                "index_above_support": True,
                "index_volume_ratio": 1.2,
                "breadth_up_count": 2000,
                "breadth_down_count": 1000,
                "main_sector_present": True,
                "sector_strength_percentile": 0.9,
                "candidate_strength_rank": 1,
                "prior_one_word_board": True,
                "limit_up_return_flow": True,
                "re_seal_confirmed": True,
            }
        }
        response = TestClient(app).post("/api/v1/research/strategies/xiaojie-leader-flow/evaluate", json=payload)
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["live_effect"], "none")
        self.assertEqual(body["boundary"], "research_only; no_automatic_order")
        self.assertEqual(body["decision"], "research_candidate")

    def test_qianlong_inputs_reach_the_evaluator_through_http(self):
        app = FastAPI()
        app.include_router(build_xiaojie_leader_flow_router(evaluate_snapshot))
        snapshot = {
            "index_above_support": True, "index_volume_ratio": 1.2, "breadth_up_count": 2000,
            "breadth_down_count": 1000, "main_sector_present": True, "sector_strength_percentile": 0.9,
            "candidate_strength_rank": 1, "breakout_or_reverse_wrap": True, "breakout_confirmed": True,
            "ma_spread_min_10d_pct": 2.0, "daily_history_complete": True, "support_or_vwap_holds": True,
            "candidate_in_main_sector": True, "fundamental_pe": -7.8, "overhead_high_distance_pct": 0.0,
            "distance_from_ma20_pct": 5.0, "pre_signal_5d_return_pct": 2.0, "sector_day_return_pct": 0.5,
            "sector_net_inflow_rate_pct": 1.0, "stock_vs_sector_divergence_pct": 4.0,
        }
        body = TestClient(app).post("/api/v1/research/strategies/xiaojie-leader-flow/evaluate",
                                    json={"snapshot": snapshot}).json()
        self.assertEqual(body["mode"], "潜龙出海_swing")
        self.assertEqual(body["qianlong_evidence"]["failed"], ["fundamental"])   # PE arrived, nothing else missing
        self.assertEqual(body["qianlong_warning"]["level"], "red")
        blocked = TestClient(app).post("/api/v1/research/strategies/xiaojie-leader-flow/evaluate",
                                       json={"snapshot": snapshot, "parameters": {"qianlong_gates_block": True}}).json()
        self.assertEqual(blocked["decision"], "no_trade")

    def test_message_features_route_reads_as_of_now_and_is_research_only(self):
        seen = {}

        async def read(**query):
            seen.update(query)
            return [{"symbol": "002640.SZ", "stance": "not_confirmed_or_risk"}]

        app = FastAPI()
        app.include_router(build_xiaojie_leader_flow_router(evaluate_snapshot, read))
        body = TestClient(app).get("/api/v1/research/strategies/xiaojie-leader-flow/message-features",
                                   params={"symbol": "002640.SZ", "instructor_only": "true"}).json()
        self.assertEqual(body["count"], 1)
        self.assertEqual(body["live_effect"], "none")
        self.assertEqual(seen["symbol"], "002640.SZ")
        self.assertTrue(seen["instructor_only"])
        self.assertIsNotNone(seen["as_of"])

    def test_unregistered_parameter_is_rejected(self):
        app = FastAPI()
        app.include_router(build_xiaojie_leader_flow_router(evaluate_snapshot))
        response = TestClient(app).post(
            "/api/v1/research/strategies/xiaojie-leader-flow/evaluate",
            json={"snapshot": {}, "parameters": {"live_weight": 1}},
        )
        self.assertEqual(response.status_code, 422)


if __name__ == "__main__":
    unittest.main()
