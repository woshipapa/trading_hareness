from __future__ import annotations

import unittest

from app import main, settings
from app.background_task_catalog import build_specs
from app.platform.runtime_task_registry import runtime_profile_owns_task, runtime_task_contract_catalog


class TacticTrialCompositionTests(unittest.TestCase):
    def test_catalog_and_registry_share_the_auction_label(self):
        self.assertIn("tactic_auction", {item["label"] for item in runtime_task_contract_catalog()})
        specs = build_specs(interval_seconds=20, enabled={"tactic_auction": True}, loops={"tactic_auction": main.tactic_auction_loop})
        self.assertEqual([spec.label for spec in specs], ["tactic_auction"])

    def test_the_auction_reading_runs_beside_the_evening_loop_not_in_the_capture_process(self):
        self.assertTrue(runtime_profile_owns_task("research", "tactic_auction"))
        self.assertFalse(runtime_profile_owns_task("intraday_edge", "tactic_auction"))
        self.assertTrue(settings.flag("TACTIC_AUCTION_ENABLED"))


if __name__ == "__main__":
    unittest.main()
