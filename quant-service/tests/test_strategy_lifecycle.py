import unittest

from app.strategy_lifecycle import assess_decay, transition_state


class StrategyLifecycleTests(unittest.TestCase):
    def test_insufficient_days_stays_outside_state_transitions(self):
        result = assess_decay({"independent_days": 3}, {"net_mean": 0.01})
        self.assertEqual(result["severity"], "insufficient")
        self.assertIn("insufficient-independent-trading-days", result["reasons"])

    def test_negative_net_mean_is_critical_and_never_live_effective(self):
        result = assess_decay(
            {"independent_days": 20, "net_mean": -0.01, "hit_rate": 0.4},
            {"net_mean": 0.02, "hit_rate": 0.6},
        )
        self.assertEqual(result["severity"], "critical")
        self.assertEqual(result["live_effect"], "none")

    def test_hysteresis_emits_manual_disable_proposal_only(self):
        result = transition_state("decayed", ["critical", "critical", "critical"], current_severity="critical")
        self.assertEqual(result["state"], "disable_proposed")
        self.assertEqual(result["proposal"], "manual_disable_review")
        self.assertEqual(result["live_effect"], "none")


if __name__ == "__main__":
    unittest.main()
