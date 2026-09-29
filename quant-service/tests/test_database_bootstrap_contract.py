from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from app.database_bootstrap_contract import ingestion_ledger_required


class DatabaseBootstrapContractTests(unittest.TestCase):
    def test_shared_mode_requires_the_feishu_ingestion_ledger(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertTrue(ingestion_ledger_required())

    def test_standalone_mode_can_use_the_explicit_compatibility_contract(self) -> None:
        with patch.dict(os.environ, {"QUANT_REQUIRE_INGESTION_LEDGER": "false"}):
            self.assertFalse(ingestion_ledger_required())


if __name__ == "__main__":
    unittest.main()
