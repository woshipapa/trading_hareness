import random
import unittest

from app.research_trials import (
    bh_q_values, evaluate_returns, family_null, parameters_hash, selection_gate,
)


class ResearchTrialTests(unittest.TestCase):
    def setUp(self):
        generator = random.Random(7)
        self.strong = [0.004 + generator.gauss(0, 0.01) for _ in range(250)]
        self.noise = [generator.gauss(0, 0.01) for _ in range(250)]

    def test_the_null_rises_with_the_number_of_variants_compared(self):
        few = family_null([0.1, 0.3])
        many = family_null([0.1, 0.3] * 25)
        self.assertEqual(few["trials"], 2)
        self.assertEqual(many["trials"], 50)
        self.assertGreater(many["expected_maximum_sharpe"], few["expected_maximum_sharpe"])

    def test_one_variant_has_no_selection_null(self):
        self.assertEqual(family_null([0.2])["expected_maximum_sharpe"], 0.0)

    def test_the_same_sharpe_deflates_more_in_a_bigger_family(self):
        alone = evaluate_returns(self.strong, null=family_null([0.3]))
        crowded = evaluate_returns(self.strong, null=family_null([0.02 * index for index in range(40)]))
        self.assertLess(crowded["deflated_sharpe"], alone["deflated_sharpe"])

    def test_a_small_sample_is_not_evaluated(self):
        result = evaluate_returns(self.strong[:19], null=family_null([0.3]))
        self.assertFalse(result["evaluable"])
        self.assertIsNone(result["deflated_sharpe"])
        self.assertEqual(selection_gate(result), "insufficient_sample")

    def test_bh_q_values_are_monotone_and_skip_missing(self):
        q = bh_q_values({"a": 0.01, "b": 0.04, "c": 0.03, "d": None})
        self.assertIsNone(q["d"])
        self.assertAlmostEqual(q["a"], 0.03)
        self.assertAlmostEqual(q["b"], 0.04)
        self.assertLessEqual(q["a"], q["c"])

    def test_the_gate_needs_both_dsr_and_fdr(self):
        base = {"evaluable": True, "deflated_sharpe": 0.97, "q_value": 0.05}
        self.assertEqual(selection_gate(base), "clears_selection_evidence_only")
        self.assertEqual(selection_gate({**base, "deflated_sharpe": 0.9}), "fails_deflated_sharpe")
        self.assertEqual(selection_gate({**base, "q_value": 0.3}), "fails_family_fdr")

    def test_noise_does_not_clear_a_family_of_twenty(self):
        null = family_null([0.05 * ((index % 7) - 3) for index in range(20)])
        self.assertLess(evaluate_returns(self.noise, null=null)["deflated_sharpe"], 0.95)

    def test_parameters_hash_is_order_independent(self):
        self.assertEqual(parameters_hash({"a": 1, "b": 2}), parameters_hash({"b": 2, "a": 1}))


if __name__ == "__main__":
    unittest.main()
