from __future__ import annotations

import unittest

from app import main
from app.background_task_catalog import build_specs
from app.platform.runtime_task_registry import runtime_task_contract_catalog
from app.runtime_executors import run_auction_capture_blocking


class AuctionPoolCompositionTests(unittest.TestCase):
    def test_runtime_and_storage_admission_use_the_auction_executor(self):
        self.assertIs(main.auction_pool_runtime.deps.run_database, run_auction_capture_blocking)
        self.assertIs(main._auction_capture_storage_admission.run_database, run_auction_capture_blocking)
        self.assertIs(main.auction_pool_runtime.deps.read_pool, main.read_auction_pool)

    def test_catalog_and_registry_share_the_auction_label(self):
        contract_labels = {item["label"] for item in runtime_task_contract_catalog()}
        self.assertIn("auction_pool_capture", contract_labels)
        specs = build_specs(
            interval_seconds=20,
            enabled={"auction_pool_capture": True},
            loops={"auction_pool_capture": main.auction_pool_capture_loop},
        )
        self.assertEqual([spec.label for spec in specs], ["auction_pool_capture"])


if __name__ == "__main__":
    unittest.main()
