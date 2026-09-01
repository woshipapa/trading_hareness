from __future__ import annotations

import unittest

from app.dragon_leader_research import (
    dragon_leader_score,
    enrich_dragon_leader_watches,
    leader_market_metrics,
    leader_playbook,
    next_session_confirmation,
    rank_dragon_leader_candidates,
    seal_quality,
)


class DragonLeaderResearchTests(unittest.TestCase):
    def test_market_metrics_fail_closed_when_prior_and_repair_evidence_missing(self) -> None:
        result = leader_market_metrics([{"ts_code": "000001.SZ", "board_count": 3}])
        self.assertEqual(result["highest_board_count"], 3)
        self.assertEqual(result["quality"], "partial")
        self.assertIsNone(result["promotion_rate"])
        self.assertIn("board_repair_rate", result["missing"])

    def test_playbook_classification_is_research_only(self) -> None:
        result = leader_playbook({"ts_code": "000001.SZ", "board_count": 4,
                                  "limit_context": {"turnover_rate": 12},
                                  "daily_features": {"volume_multiple_5d": 1.4},
                                  "dragon_leader_watch": {"leader_rank": 1}})
        self.assertEqual(result["status"], "partial_shadow")
        self.assertEqual(result["live_effect"], "none")
        self.assertTrue(result["plans"][2]["eligible"])
        self.assertEqual(result["next_session_confirmation_schema"]["status"], "not_observed")

    def test_seal_quality_fails_closed_when_book_events_are_missing(self) -> None:
        result = seal_quality({"ts_code": "000001.SZ", "limit_context": {}})
        self.assertEqual(result["status"], "not_observed")
        self.assertIsNone(result["score"])
        self.assertEqual(result["live_effect"], "none")

    def test_seal_quality_scores_only_observed_fields(self) -> None:
        result = seal_quality({"limit_context": {
            "first_seal_time": "09:35:00", "break_count": 1,
            "break_duration_minutes": 4, "reseal_latency_minutes": 3,
        }})
        self.assertEqual(result["status"], "partial")
        self.assertGreater(result["score"], 0)
        self.assertIn("close_seal_ratio", result["missing"])

    def test_next_session_confirmation_requires_complete_evidence(self) -> None:
        missing = next_session_confirmation(auction_gap_pct=3.0, expected_gap_pct=2.0)
        self.assertFalse(missing["confirmed"])
        self.assertEqual(missing["status"], "not_observed")
        confirmed = next_session_confirmation(
            auction_gap_pct=3.0, expected_gap_pct=2.0,
            first_15m_amount_ratio=0.15, theme_relative_strength_pct=1.2,
        )
        self.assertTrue(confirmed["confirmed"])
        self.assertEqual(confirmed["status"], "confirmed")

    def test_score_shadow_exposes_components_and_stays_non_live(self) -> None:
        item = {
            "ts_code": "000001.SZ", "board_count": 3, "status": "T字板",
            "limit_context": {"turnover_rate": 18.0},
            "daily_features": {"volume_multiple_5d": 1.8},
            "board_context": {"exact_member_mapping": True, "flow_percentile": 0.9},
            "lhb_context": {"institution_net_buy": -1_000_000},
            "dragon_leader_watch": {"leader_rank": 1, "theme_context": {
                "observable_limit_up_count": 4, "observable_multi_board_count": 2,
            }},
        }
        score = dragon_leader_score(item, market={"highest_observed_streak": 3, "observable_multi_board_count": 3})
        self.assertEqual(score["status"], "partial_shadow")
        self.assertEqual(score["live_effect"], "none")
        self.assertIsNone(score["components"]["intraday_confirmation"]["score"])
        self.assertIn("lhb_institution_net_sell", score["risk_flags"])
        self.assertGreater(score["score"], 0)

    def test_nested_limit_context_streak_is_used_by_score_and_market_context(self) -> None:
        items = [{
            "ts_code": "000001.SZ",
            "limit_context": {
                "streak_count": 4,
                "continuation_watch": {"eligible": True, "streak_count": 4, "seal_to_float": 0.03},
            },
            "board_context": {},
        }]
        market = enrich_dragon_leader_watches(items)
        self.assertEqual(market["highest_observed_streak"], 4)
        self.assertEqual(market["observable_multi_board_count"], 1)
        self.assertEqual(items[0]["dragon_leader_watch"]["streak_count"], 4)

    def test_compact_continuation_tag_is_counted(self) -> None:
        from app.post_close_limit_features import board_count
        self.assertEqual(board_count("6连板"), 6)

    def test_theme_ladder_is_a_manual_review_not_an_order(self) -> None:
        items = [
            {
                "ts_code": "000001.SZ", "board_count": 3, "limit_amount": 30_000_000, "status": "T字板",
                "continuation_watch": {"eligible": True, "streak_count": 3, "seal_to_float": 0.03},
                "board_context": {"exact_member_mapping": True, "sector_key": "885001.TI", "label": "测试主题", "net_amount": 100},
            },
            {
                "ts_code": "000002.SZ", "board_count": 2, "limit_amount": 20_000_000,
                "continuation_watch": {"eligible": True, "streak_count": 2, "seal_to_float": 0.02},
                "board_context": {"exact_member_mapping": True, "sector_key": "885001.TI", "label": "测试主题", "net_amount": 100},
            },
        ]

        market = enrich_dragon_leader_watches(items)
        leader = items[0]["dragon_leader_watch"]

        self.assertEqual(market["status"], "partial_post_close_limit_up_union")
        self.assertEqual(leader["leader_rank"], 1)
        self.assertEqual(leader["review_tier"], "theme_ladder_manual_review")
        self.assertEqual(leader["session_confirmation"]["status"], "not_observed")
        self.assertIn("no_automatic_order", leader["risk_flags"])

    def test_missing_theme_mapping_stays_visible_with_coverage_risk(self) -> None:
        items = [{
            "ts_code": "000003.SZ", "board_count": 2, "limit_amount": 10_000_000, "status": "一字板",
            "continuation_watch": {"eligible": True, "streak_count": 2, "seal_to_float": 0.02}, "board_context": {},
        }]

        enrich_dragon_leader_watches(items)
        leader = items[0]["dragon_leader_watch"]

        self.assertEqual(leader["theme_context"]["status"], "unavailable")
        self.assertIn("exact_theme_ladder_unavailable", leader["risk_flags"])
        self.assertIn("one_word_board_not_entry", leader["risk_flags"])
        self.assertEqual(rank_dragon_leader_candidates(items)[0]["dragon_leader_watch"]["rank"], 1)

    def test_first_board_is_filtered_even_when_the_market_context_exists(self) -> None:
        items = [{
            "ts_code": "000004.SZ", "board_count": 1,
            "continuation_watch": {"eligible": False, "streak_count": 1, "seal_to_float": 0.04}, "board_context": {},
        }]

        market = enrich_dragon_leader_watches(items)

        self.assertEqual(market["market_state"], "first_board_dominated")
        self.assertEqual(items[0]["dragon_leader_watch"]["status"], "filtered")
        self.assertEqual(rank_dragon_leader_candidates(items), [])


if __name__ == "__main__":
    unittest.main()
