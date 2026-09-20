from __future__ import annotations

import io
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parent / "shared-peer" / "verify-owner-cutover.py"
SPEC = importlib.util.spec_from_file_location("verify_owner_cutover", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class OwnerCutoverVerifierTests(unittest.TestCase):
    def test_storage_checks_accept_only_the_complete_atomic_contract(self) -> None:
        checks = MODULE.storage_checks({
            "status": "layered",
            "cutover_ready": True,
            "issues": [],
            "cold_schema": {
                "atomic_read_enabled": True,
                "eligible_tables": [f"table_{index}_cold" for index in range(5)],
            },
            "legacy_source_records": {"ready": True, "tablespace": "stock_cold"},
            "adjustment_semantics": {
                "ready": True, "status": "ready", "issues": [],
                "guard_indexes": {"status": "ready", "missing": []},
                "data_guard": {"status": "ready", "invalid_relations": []},
            },
        })
        self.assertTrue(all(check["ok"] for check in checks.values()))

    def test_partial_storage_is_a_blocking_receipt(self) -> None:
        checks = MODULE.storage_checks({
            "status": "partial_cutover",
            "cutover_ready": False,
            "issues": ["canonical_bars_daily:cold_missing"],
            "cold_schema": {"atomic_read_enabled": False, "eligible_tables": []},
            "legacy_source_records": {"ready": False, "tablespace": None},
            "adjustment_semantics": {
                "ready": False,
                "status": "legacy",
                "issues": ["daily_adjustment_factors:adj_factor_not_nullable"],
            },
        })
        self.assertFalse(checks["owner_cutover"]["ok"])
        self.assertFalse(checks["cold_atomic_read"]["ok"])
        self.assertFalse(checks["legacy_cold_placement"]["ok"])
        self.assertFalse(checks["adjustment_semantics"]["ok"])

    def test_batch_readback_requires_alembic_lineage_evidence(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("quant.alembic_version", source)
        self.assertIn('"database": database', source)
        self.assertIn('"role": role', source)
        self.assertIn("role_non_superuser", source)
        self.assertIn("role_no_replication", source)
        self.assertIn("role_no_bypassrls", source)
        self.assertIn("statement_timeout_15m", source)
        self.assertIn("idle_transaction_timeout_5m", source)
        self.assertIn("role_noinherit", source)
        self.assertIn("no_role_memberships", source)
        self.assertIn("rolinherit", source)
        self.assertIn("role_membership_count", source)
        self.assertIn("role_membership_sample", source)
        self.assertIn("database_not_owned", source)
        self.assertIn("schema_not_owned", source)
        self.assertIn("quant_objects_not_owned", source)
        self.assertIn("database_owned", source)
        self.assertIn("schema_owned", source)
        self.assertIn("quant_objects_owned", source)
        self.assertIn("quant_owned_object_count", source)
        self.assertIn("quant_owned_object_sample", source)
        self.assertIn("quant_security_definer_executable_count", source)
        self.assertIn("quant_writable_relation_count", source)
        self.assertIn("quant_writable_relation_sample", source)
        self.assertIn("quant_writable_sequence_count", source)
        self.assertIn("quant_writable_sequence_sample", source)
        self.assertIn("no_quant_relation_dml", source)
        self.assertIn("no_quant_sequence_writes", source)
        self.assertIn("schema_not_creatable", source)
        self.assertIn("table_not_insertable", source)
        self.assertIn("alembic_version_present", source)
        self.assertIn('"requirements": required', source)

    def test_lane_lineage_rejects_database_or_revision_mismatch(self) -> None:
        api = {"ok": True, "database_name": "owner", "alembic_version": "v2"}
        self.assertTrue(MODULE.compare_lane_lineage(
            api, {"ok": True, "database": "owner", "alembic_version": "v2"}
        )["ok"])
        self.assertFalse(MODULE.compare_lane_lineage(
            api, {"ok": True, "database": "other", "alembic_version": "v2"}
        )["ok"])
        self.assertFalse(MODULE.compare_lane_lineage(
            api, {"ok": True, "database": "owner", "alembic_version": "v1"}
        )["ok"])

    def test_batch_read_only_requirements_include_noinherit(self) -> None:
        receipt = {
            "database_present": True,
            "canonical_present": True,
            "alembic_version_present": True,
            "alembic_version": "owner-v2",
            "role_superuser": False,
            "role_createdb": False,
            "role_createrole": False,
            "role_replication": False,
            "role_bypassrls": False,
            "role_inherit": False,
            "statement_timeout_ms": 900_000,
            "idle_transaction_timeout_ms": 300_000,
            "role_membership_count": 0,
            "database_owned": False,
            "schema_owned": False,
            "quant_objects_owned": False,
            "quant_security_definer_executable_count": 0,
            "quant_writable_relation_count": 0,
            "quant_writable_sequence_count": 0,
            "schema_creatable": False,
            "table_insertable": False,
            "table_updatable": False,
            "table_deletable": False,
        }
        requirements = MODULE.batch_read_only_requirements(receipt)
        self.assertTrue(all(requirements.values()))
        receipt["role_inherit"] = True
        self.assertFalse(MODULE.batch_read_only_requirements(receipt)["role_noinherit"])
        receipt["role_inherit"] = False
        receipt["quant_writable_relation_count"] = 1
        self.assertFalse(MODULE.batch_read_only_requirements(receipt)["no_quant_relation_dml"])
        receipt["quant_writable_relation_count"] = 0
        receipt.pop("quant_objects_owned")
        self.assertFalse(MODULE.batch_read_only_requirements(receipt)["quant_objects_not_owned"])

    def test_verifier_requires_non_placeholder_build_provenance(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("def api_build", source)
        self.assertIn('release.lower() not in {"unknown", "unset"}', source)
        self.assertIn("--expected-release", source)
        self.assertIn("api_database_lineage", source)
        self.assertIn("lane_lineage", source)
        self.assertIn("EXPECTED_REPLAY_COVERAGE_DEFINITION", source)
        self.assertIn("replay_readiness_contract", source)
        self.assertIn("semantics_startup_policy", source)
        self.assertIn("api/v1/peer/contract", source)
        self.assertIn("api/v1/peer/errors", source)

    def test_build_receipt_rejects_unknown_and_accepts_pinned_release(self) -> None:
        verifier = MODULE.Verifier(Path("."), "http://example", "127.0.0.1", 15433, "release-1")

        def response(payload):
            return io.BytesIO(json.dumps(payload).encode())

        with patch.object(MODULE, "urlopen", return_value=response({"status": "ok", "build": {"release": "unknown"}})):
            self.assertFalse(verifier.api_build()["ok"])
        with patch.object(MODULE, "urlopen", return_value=response({"status": "ok", "build": {"release": "unset"}})):
            self.assertFalse(verifier.api_build()["ok"])
        with patch.object(MODULE, "urlopen", return_value=response({"status": "ok", "build": {"release": "release-1"}})):
            self.assertTrue(verifier.api_build()["ok"])

    def test_replay_readiness_contract_rejects_old_definition(self) -> None:
        verifier = MODULE.Verifier(Path("."), "http://example", "127.0.0.1", 15433)

        def response(payload):
            return io.BytesIO(json.dumps(payload).encode())

        old = {"status": "blocked", "coverage_definition": "point_in_time_all_a_membership_with_daily_bars_fundamentals_and_trade_limits_at_80pct_min_1000"}
        current = {"status": "blocked", "coverage_definition": MODULE.EXPECTED_REPLAY_COVERAGE_DEFINITION}
        with patch.object(MODULE, "urlopen", return_value=response(old)):
            self.assertFalse(verifier.replay_readiness_contract()["ok"])
        with patch.object(MODULE, "urlopen", return_value=response(current)):
            self.assertTrue(verifier.replay_readiness_contract()["ok"])

    def test_semantics_startup_policy_requires_both_quant_processes(self) -> None:
        verifier = MODULE.Verifier(Path("."), "http://example", "127.0.0.1", 15433)
        with patch.object(verifier, "service_environment", side_effect=[
            {"PEER_REQUIRE_OWNER_SEMANTICS": "true"},
            {"PEER_REQUIRE_OWNER_SEMANTICS": "false"},
        ]):
            checks = verifier.semantics_policy()
        self.assertFalse(checks["ok"])
        self.assertFalse(checks["services"]["quant-research-scheduler"]["ok"])

    def test_expected_release_can_be_read_from_non_secret_compose_env(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text("PEER_EXPECTED_RELEASE=release-2\nPEER_DB_PASSWORD=hidden\n", encoding="utf-8")
            self.assertEqual(MODULE.compose_env_value(path.parent, "PEER_EXPECTED_RELEASE"), "release-2")

    def test_storage_keeps_api_lineage_for_lane_comparison(self) -> None:
        verifier = MODULE.Verifier(Path("."), "http://example", "127.0.0.1", 15433)

        def response(payload):
            return io.BytesIO(json.dumps(payload).encode())

        payload = {
            "status": "layered", "cutover_ready": True,
            "cold_schema": {"atomic_read_enabled": True, "eligible_tables": ["a", "b", "c", "d", "e"]},
            "legacy_source_records": {"ready": True},
            "adjustment_semantics": {
                "ready": True,
                "guard_indexes": {"status": "ready", "missing": []},
                "data_guard": {"status": "ready", "invalid_relations": []},
            },
            "database_lineage": {"status": "ready", "database_name": "owner", "alembic_version": "v2"},
        }
        with patch.object(MODULE, "urlopen", return_value=response(payload)):
            checks = verifier.storage()
        self.assertTrue(checks["api_database_lineage"]["ok"])
        self.assertEqual(checks["api_database_lineage"]["alembic_version"], "v2")


if __name__ == "__main__":
    unittest.main()
