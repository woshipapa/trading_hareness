import os
import unittest
from unittest.mock import patch

from entrypoint import (
    migrations_enabled,
    owner_cutover_required,
    owner_semantics_required,
    validate_owner_cutover_status,
    validate_owner_semantics_status,
)


class EntrypointTests(unittest.TestCase):
    def test_peer_can_explicitly_skip_migrations(self):
        with patch.dict(os.environ, {"QUANT_SKIP_MIGRATIONS": "true"}, clear=False):
            self.assertFalse(migrations_enabled())

    def test_migrations_remain_enabled_by_default(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertTrue(migrations_enabled())

    def test_batch_lane_requires_owner_cutover_by_default(self):
        with patch.dict(os.environ, {"PGHOST": "db-tunnel", "PGPORT": "5433"}, clear=True):
            self.assertTrue(owner_cutover_required())

    def test_auto_value_keeps_batch_lane_inferred(self):
        with patch.dict(os.environ, {"PEER_REQUIRE_OWNER_CUTOVER": "auto", "PGHOST": "db-tunnel", "PGPORT": "5433"}, clear=True):
            self.assertTrue(owner_cutover_required())

    def test_explicit_false_can_be_used_for_a_diagnostic_process(self):
        with patch.dict(os.environ, {"PEER_REQUIRE_OWNER_CUTOVER": "false", "PGHOST": "db-tunnel", "PGPORT": "5433"}, clear=True):
            self.assertFalse(owner_cutover_required())

    def test_legacy_lane_does_not_require_owner_cutover(self):
        with patch.dict(os.environ, {"PGHOST": "db-tunnel", "PGPORT": "5432"}, clear=True):
            self.assertFalse(owner_cutover_required())

    def test_semantics_gate_is_explicit_and_off_by_default(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(owner_semantics_required())
        with patch.dict(os.environ, {"PEER_REQUIRE_OWNER_SEMANTICS": "true"}, clear=True):
            self.assertTrue(owner_semantics_required())

    def test_non_ready_owner_semantics_are_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "owner runtime schema"):
            validate_owner_semantics_status({
                "issues": ["canonical_bars_daily:missing:adjustment_state"],
                "adjustment_semantics": {
                    "ready": False,
                    "issues": ["canonical_bars_daily:missing:adjustment_state"],
                    "data_guard": {"status": "not_evaluated"},
                },
            })

    def test_ready_owner_semantics_are_accepted(self):
        validate_owner_semantics_status({
            "adjustment_semantics": {
                "ready": True,
                "guard_indexes": {"status": "ready"},
                "data_guard": {"status": "ready"},
            },
        })

    def test_owner_v2_does_not_require_guard_indexes(self):
        validate_owner_semantics_status({
            "adjustment_semantics": {
                "ready": True,
                "guard_indexes": {"status": "not_applicable"},
                "data_guard": {"status": "not_applicable"},
            },
        })

    def test_non_layered_status_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "owner runtime schema"):
            validate_owner_cutover_status({"status": "partial_cutover", "issues": ["cold_missing"]})

    def test_layered_status_is_accepted(self):
        validate_owner_cutover_status({"status": "layered", "cutover_ready": True})


if __name__ == "__main__":
    unittest.main()
