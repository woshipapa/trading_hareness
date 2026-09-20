import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.owner_peer_contract import (
    OwnerPeerContractError,
    contract_mode,
    validate_contract,
    verify_owner_peer_contract,
)


CONTRACT = {
    "alembic_head": "20260919_0106",
    "objects": [
        {"name": "quant.canonical_bars_daily", "columns": ["symbol", "trading_date", "adj_factor", "available_at", "quality_status"]},
        {"name": "quant.daily_adjustment_factors", "columns": ["symbol", "trading_date", "adj_factor", "provider", "available_at", "raw"]},
    ],
    "cold_tier": {"tables": ["quant.legacy_source_records"], "peer_readable": False},
    "enumerations": {"factor_semantics": ["corporate_action_cumulative", "same_day_identity_only"]},
    "not_provided": [],
    "endpoints": [],
    "rules": [],
}


class OwnerPeerContractTests(unittest.TestCase):
    def test_contract_requires_machine_readable_envelope_only(self):
        self.assertEqual(validate_contract(CONTRACT), [])
        self.assertIn("missing_alembic_head", validate_contract({"objects": []}))

    def test_report_only_writes_receipt_but_does_not_block_first_run(self):
        with tempfile.TemporaryDirectory() as directory:
            env = {
                "PEER_OWNER_CONTRACT_MODE": "report_only",
                "PEER_OWNER_CONTRACT_RECEIPT_PATH": str(Path(directory) / "receipt.json"),
                "QUANT_SHARED_READ_API_BASE_URL": "http://owner:5681",
                "QUANT_SHARED_READ_API_KEY": "redacted-test-key",
            }
            with patch("app.owner_peer_contract.requests.get") as get:
                get.return_value.raise_for_status.return_value = None
                get.return_value.json.return_value = CONTRACT
                result = verify_owner_peer_contract(required=True, environ=env)
            self.assertEqual(result["alembic_head"], "20260919_0106")
            self.assertEqual(json.loads(Path(env["PEER_OWNER_CONTRACT_RECEIPT_PATH"]).read_text())["status"], "passed")

    def test_block_mode_needs_a_prior_receipt(self):
        with tempfile.TemporaryDirectory() as directory:
            env = {
                "PEER_OWNER_CONTRACT_MODE": "block",
                "PEER_OWNER_CONTRACT_RECEIPT_PATH": str(Path(directory) / "receipt.json"),
                "QUANT_SHARED_READ_API_BASE_URL": "http://owner:5681",
                "QUANT_SHARED_READ_API_KEY": "redacted-test-key",
            }
            with patch("app.owner_peer_contract.requests.get") as get:
                get.return_value.raise_for_status.return_value = None
                get.return_value.json.return_value = CONTRACT
                verify_owner_peer_contract(required=True, environ=env)
                self.assertIsNotNone(verify_owner_peer_contract(required=True, environ=env))

    def test_block_mode_fails_closed_on_unavailable_owner(self):
        env = {
            "PEER_OWNER_CONTRACT_MODE": "block",
            "QUANT_SHARED_READ_API_BASE_URL": "http://owner:5681",
            "QUANT_SHARED_READ_API_KEY": "redacted-test-key",
        }
        with patch("app.owner_peer_contract.requests.get", side_effect=OSError("offline")):
            with self.assertRaises(OwnerPeerContractError):
                verify_owner_peer_contract(required=True, environ=env)

    def test_mode_defaults_to_report_only(self):
        self.assertEqual(contract_mode({}), "report_only")

    def test_repository_has_no_parameter_after_interval_keyword(self):
        app_root = Path(__file__).parents[1] / "app"
        offenders = []
        for path in app_root.rglob("*.py"):
            if re.search(r"\binterval\s+\$\d+", path.read_text(encoding="utf-8"), flags=re.IGNORECASE):
                offenders.append(str(path))
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
