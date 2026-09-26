#!/usr/bin/env python3
"""Fail-closed verification for the owner contract and batch-lane cutover.

Run ``--stage lane`` after installing the owner 15433 task and exposing the
peer db-tunnel:5433 forward. Run the default ``--stage complete`` after
switching the research scheduler to db-tunnel:5433. The script is read-only and never
prints database credentials. The 5433 read-back proves that the peer database
role is not a superuser and has the owner-mandated statement timeouts. The
owner intentionally retains wide write grants for peer workloads; observed
write capabilities are reported for reconciliation, not used as a release
gate. The historical storage-tiers projection is diagnostic only and is not a
release gate.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
from pathlib import Path
from typing import Any
from urllib.request import urlopen


EXPECTED_REPLAY_COVERAGE_DEFINITION = (
    "point_in_time_all_a_membership_with_complete_adjusted_daily_bars_"
    "fundamentals_and_trade_limits_at_80pct_min_1000"
)


def storage_checks(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    runtime = payload.get("owner_runtime_schema") or payload
    if runtime.get("status") == "owner_compatible":
        lineage = runtime.get("database_lineage") or {}
        return {
            "owner_cutover": {
                "ok": runtime.get("schema_ready") is True,
                "status": runtime.get("status"),
                "issues": runtime.get("issues") or [],
            },
            "owner_runtime_schema": {
                "ok": runtime.get("schema_ready") is True,
                "required_columns": runtime.get("required_columns") or {},
                "factor_semantics": runtime.get("factor_semantics") or {},
            },
            "cold_layer": {
                "ok": (runtime.get("cold_layer") or {}).get("peer_dependency") is False,
                "policy": (runtime.get("cold_layer") or {}).get("policy"),
            },
            "api_database_lineage": {
                "ok": lineage.get("status") == "ready",
                "database_name": lineage.get("database_name"),
                "alembic_version": lineage.get("alembic_version"),
                "status": lineage.get("status"),
                "reason": lineage.get("reason"),
            },
        }
    cold = payload.get("cold_schema") or {}
    semantics = payload.get("adjustment_semantics") or {}
    legacy = payload.get("legacy_source_records") or {}
    return {
        "owner_cutover": {
            "ok": payload.get("status") == "layered" and payload.get("cutover_ready") is True,
            "status": payload.get("status"),
            "issues": payload.get("issues") or [],
        },
        "cold_atomic_read": {
            "ok": cold.get("atomic_read_enabled") is True and len(cold.get("eligible_tables") or []) == 5,
            "eligible_tables": cold.get("eligible_tables") or [],
        },
        "legacy_cold_placement": {
            "ok": legacy.get("ready") is True,
            "tablespace": legacy.get("tablespace"),
        },
        "adjustment_semantics": {
            "ok": semantics.get("ready") is True
                  and (semantics.get("guard_indexes") or {}).get("status") == "ready"
                  and (semantics.get("data_guard") or {}).get("status") == "ready"
                  and not (semantics.get("data_guard") or {}).get("invalid_relations"),
            "status": semantics.get("status"),
            "issues": semantics.get("issues") or [],
            "guard_indexes": semantics.get("guard_indexes") or {},
            "data_guard": semantics.get("data_guard") or {},
        },
    }


def compare_lane_lineage(
    api_lineage: dict[str, Any], batch_lineage: dict[str, Any]
) -> dict[str, Any]:
    """Require the API and bulk SQL lanes to identify the same owner DB."""
    return {
        "ok": (
            api_lineage.get("ok") is True
            and batch_lineage.get("ok") is True
            and api_lineage.get("database_name") == batch_lineage.get("database")
            and api_lineage.get("alembic_version") == batch_lineage.get("alembic_version")
        ),
        "api_database_name": api_lineage.get("database_name"),
        "batch_database_name": batch_lineage.get("database"),
        "api_alembic_version": api_lineage.get("alembic_version"),
        "batch_alembic_version": batch_lineage.get("alembic_version"),
    }


def batch_database_requirements(receipt: dict[str, Any]) -> dict[str, bool]:
    """Evaluate the enforced owner role contract from one SQL receipt."""
    return {
        "database_present": receipt.get("database_present") is True,
        "canonical_present": receipt.get("canonical_present") is True,
        "alembic_version_present": receipt.get("alembic_version_present") is True,
        "alembic_version": bool(receipt.get("alembic_version")),
        "role_non_superuser": receipt.get("role_superuser") is False,
        "role_no_createdb": receipt.get("role_createdb") is False,
        "role_no_createrole": receipt.get("role_createrole") is False,
        "role_no_replication": receipt.get("role_replication") is False,
        "role_no_bypassrls": receipt.get("role_bypassrls") is False,
        "statement_timeout_15m": receipt.get("statement_timeout_ms") == 900_000,
        "idle_transaction_timeout_5m": receipt.get("idle_transaction_timeout_ms") == 300_000,
        "schema_not_owned": receipt.get("schema_owned") is False,
        "no_security_definer_functions": receipt.get("quant_security_definer_executable_count") == 0,
        "write_capabilities_observed": isinstance(receipt.get("quant_writable_relation_count"), int)
        and isinstance(receipt.get("quant_writable_sequence_count"), int),
        "role_membership_observed": isinstance(receipt.get("role_membership_count"), int),
        # Older recorded receipts predate the two-port check; a live receipt
        # includes these keys and a false value still blocks the verifier.
        "session_tunnel_5432": receipt.get("tunnel_5432", True) is True,
        "batch_tunnel_5433": receipt.get("tunnel_5433", True) is True,
    }


def compose_env_value(compose_dir: Path, key: str) -> str | None:
    """Read one non-secret operator pin without loading the full env file."""
    path = compose_dir / ".env"
    if not path.is_file():
        return None
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        if name.strip() == key:
            value = value.strip().strip("\"'")
            return value or None
    return None


class Verifier:
    def __init__(self, compose_dir: Path, api_base: str, batch_host: str, batch_port: int,
                 expected_release: str | None = None) -> None:
        self.compose_dir = compose_dir
        self.api_base = api_base.rstrip("/")
        self.batch_host = batch_host
        self.batch_port = batch_port
        self.expected_release = expected_release.strip() if expected_release else None
        self.compose = [
            "docker", "compose", "--env-file", str(compose_dir / ".env"),
            "-f", str(compose_dir / "compose.yaml"),
            "-f", str(compose_dir / "compose.intraday-owner.yaml"),
        ]

    def command(self, args: list[str], timeout: int = 20) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            args, text=True, capture_output=True, timeout=timeout, check=False,
        )

    def service_container(self, service: str) -> str:
        result = self.command([*self.compose, "ps", "-q", service])
        return result.stdout.strip() if result.returncode == 0 else ""

    def container_health(self, service: str) -> dict[str, Any]:
        container = self.service_container(service)
        if not container:
            return {"ok": False, "service": service, "state": "missing"}
        result = self.command([
            "docker", "inspect", "--format",
            "{{.State.Status}}|{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}",
            container,
        ])
        value = result.stdout.strip()
        state, _, health = value.partition("|")
        return {
            "ok": result.returncode == 0 and state == "running" and health == "healthy",
            "service": service,
            "state": state or "unknown",
            "health": health or "unknown",
        }

    def scheduler_lane(self) -> dict[str, Any]:
        container = self.service_container("quant-research-scheduler")
        if not container:
            return {"ok": False, "state": "missing"}
        result = self.command([
            "docker", "inspect", "--format", "{{range .Config.Env}}{{println .}}{{end}}", container,
        ])
        values = {}
        for line in result.stdout.splitlines():
            key, separator, value = line.partition("=")
            if separator and key in {"PGHOST", "PGPORT", "QUANT_SKIP_MIGRATIONS"}:
                values[key] = value
        return {
            "ok": (
                result.returncode == 0
                and values.get("PGHOST") == "db-tunnel"
                and values.get("PGPORT") == "5433"
                and values.get("QUANT_SKIP_MIGRATIONS") == "true"
            ),
            "host": values.get("PGHOST"),
            "port": values.get("PGPORT"),
            "skip_migrations": values.get("QUANT_SKIP_MIGRATIONS"),
        }

    def service_environment(self, service: str) -> dict[str, str]:
        container = self.service_container(service)
        if not container:
            return {}
        result = self.command([
            "docker", "inspect", "--format", "{{range .Config.Env}}{{println .}}{{end}}", container,
        ])
        values: dict[str, str] = {}
        for line in result.stdout.splitlines():
            key, separator, value = line.partition("=")
            if separator:
                values[key] = value
        return values

    def semantics_policy(self) -> dict[str, Any]:
        """Require every owner-bound quant process to fail closed pre-start."""
        services = {}
        for service in ("quant-research", "quant-research-scheduler"):
            values = self.service_environment(service)
            services[service] = {
                "ok": values.get("PEER_REQUIRE_OWNER_SEMANTICS") == "true",
                "require_owner_semantics": values.get("PEER_REQUIRE_OWNER_SEMANTICS"),
            }
        return {"ok": bool(services) and all(item["ok"] for item in services.values()), "services": services}

    def batch_listener(self) -> dict[str, Any]:
        try:
            with socket.create_connection((self.batch_host, self.batch_port), timeout=5):
                return {"ok": True, "host": self.batch_host, "port": self.batch_port}
        except OSError as error:
            return {
                "ok": False, "host": self.batch_host, "port": self.batch_port,
                "error": f"{type(error).__name__}: {error}",
            }

    def batch_database_readback(self) -> dict[str, Any]:
        container = self.service_container("quant-research")
        if not container:
            return {"ok": False, "state": "quant_container_missing"}
        program = """
import os
import psycopg
ports = {}
for _port in (5432, 5433):
    try:
        with psycopg.connect(
            host="db-tunnel", port=_port,
            dbname=os.environ["PGDATABASE"], user=os.environ["PGUSER"], password=os.environ["PGPASSWORD"],
            connect_timeout=5, application_name="peer_cutover_port_check",
        ) as _connection:
            _value = _connection.execute("SELECT 1, inet_server_port()").fetchone()
            ports[_port] = bool(_value and _value[0] == 1 and int(_value[1]) == 55432)
    except Exception:
        ports[_port] = False
connection = psycopg.connect(
    host="db-tunnel", port=5433,
    dbname=os.environ["PGDATABASE"], user=os.environ["PGUSER"], password=os.environ["PGPASSWORD"],
    connect_timeout=5, application_name="peer_cutover_verify",
)
with connection:
    with connection.cursor() as cursor:
        cursor.execute('''SELECT
                            (SELECT setting::bigint FROM pg_settings WHERE name='statement_timeout'),
                            (SELECT setting::bigint FROM pg_settings WHERE name='idle_in_transaction_session_timeout')''')
        statement_timeout_ms, idle_transaction_timeout_ms = cursor.fetchone()
        cursor.execute("SET LOCAL statement_timeout='5s'")
        cursor.execute('''SELECT current_database(),
                                 current_user,
                                 to_regclass('quant.canonical_bars_daily') IS NOT NULL,
                                 to_regclass('quant.alembic_version') IS NOT NULL,
                                 COALESCE((SELECT rolsuper FROM pg_roles WHERE rolname=current_user), true),
                                 COALESCE((SELECT rolcreatedb FROM pg_roles WHERE rolname=current_user), true),
                                 COALESCE((SELECT rolcreaterole FROM pg_roles WHERE rolname=current_user), true),
                                 COALESCE((SELECT rolreplication FROM pg_roles WHERE rolname=current_user), true),
                                 COALESCE((SELECT rolbypassrls FROM pg_roles WHERE rolname=current_user), true),
                                 COALESCE((SELECT rolinherit FROM pg_roles WHERE rolname=current_user), true),
                                 COALESCE((SELECT count(*)::int FROM pg_auth_members m
                                             JOIN pg_roles child ON child.oid=m.member
                                            WHERE child.rolname=current_user), 0),
                                 COALESCE((SELECT datdba = (SELECT oid FROM pg_roles WHERE rolname=current_user)
                                             FROM pg_database WHERE datname=current_database()), true),
                                 COALESCE((SELECT nspowner = (SELECT oid FROM pg_roles WHERE rolname=current_user)
                                             FROM pg_namespace WHERE nspname='quant'), true),
                                 COALESCE(EXISTS (SELECT 1 FROM pg_class c
                                                    JOIN pg_namespace n ON n.oid=c.relnamespace
                                                   WHERE n.nspname='quant'
                                                     AND c.relowner=(SELECT oid FROM pg_roles WHERE rolname=current_user)), true),
                                 COALESCE((SELECT count(*)::int FROM pg_class c
                                             JOIN pg_namespace n ON n.oid=c.relnamespace
                                            WHERE n.nspname='quant'
                                              AND c.relowner=(SELECT oid FROM pg_roles WHERE rolname=current_user)), 0),
                                 COALESCE((SELECT count(*)::int FROM pg_proc p
                                             JOIN pg_namespace n ON n.oid=p.pronamespace
                                            WHERE n.nspname='quant' AND p.prosecdef
                                              AND has_function_privilege(current_user,p.oid,'EXECUTE')), 0),
                                 COALESCE((SELECT count(*)::int FROM pg_class c
                                             JOIN pg_namespace n ON n.oid=c.relnamespace
                                            WHERE n.nspname='quant'
                                              AND c.relkind IN ('r','p','v','m','f')
                                              AND (has_table_privilege(current_user,c.oid,'INSERT')
                                                OR has_table_privilege(current_user,c.oid,'UPDATE')
                                                OR has_table_privilege(current_user,c.oid,'DELETE')
                                                OR has_table_privilege(current_user,c.oid,'TRUNCATE')
                                                OR has_table_privilege(current_user,c.oid,'REFERENCES')
                                                OR has_table_privilege(current_user,c.oid,'TRIGGER'))), 0),
                                 COALESCE((SELECT count(*)::int FROM pg_class c
                                             JOIN pg_namespace n ON n.oid=c.relnamespace
                                            WHERE n.nspname='quant' AND c.relkind='S'
                                              AND (has_sequence_privilege(current_user,c.oid,'USAGE')
                                                OR has_sequence_privilege(current_user,c.oid,'UPDATE'))), 0),
                                 has_schema_privilege(current_user, 'quant', 'CREATE'),
                                 CASE WHEN to_regclass('quant.canonical_bars_daily') IS NULL THEN false
                                      ELSE has_table_privilege(current_user, 'quant.canonical_bars_daily', 'INSERT') END,
                                 CASE WHEN to_regclass('quant.canonical_bars_daily') IS NULL THEN false
                                      ELSE has_table_privilege(current_user, 'quant.canonical_bars_daily', 'UPDATE') END,
                                 CASE WHEN to_regclass('quant.canonical_bars_daily') IS NULL THEN false
                                      ELSE has_table_privilege(current_user, 'quant.canonical_bars_daily', 'DELETE') END''')
        (
            database, role, canonical_present, alembic_present,
            role_superuser, role_createdb, role_createrole,
            role_replication, role_bypassrls, role_inherit,
            role_membership_count,
            database_owned, schema_owned, quant_objects_owned, quant_owned_object_count,
            quant_security_definer_executable_count, quant_writable_relation_count,
            quant_writable_sequence_count, schema_creatable,
            table_insertable, table_updatable, table_deletable,
        ) = cursor.fetchone()
        versions = []
        if alembic_present:
            cursor.execute("SELECT version_num FROM quant.alembic_version ORDER BY version_num")
            versions = [str(row[0]) for row in cursor.fetchall() if row[0] is not None]
        cursor.execute('''SELECT parent.rolname
                          FROM pg_auth_members m
                          JOIN pg_roles child ON child.oid=m.member
                          JOIN pg_roles parent ON parent.oid=m.roleid
                         WHERE child.rolname=current_user
                         ORDER BY parent.rolname LIMIT 10''')
        role_membership_sample = [str(row[0]) for row in cursor.fetchall()]
        cursor.execute('''SELECT n.nspname || '.' || c.relname
                          FROM pg_class c
                          JOIN pg_namespace n ON n.oid=c.relnamespace
                         WHERE n.nspname='quant'
                           AND c.relkind IN ('r','p','v','m','f')
                           AND (has_table_privilege(current_user,c.oid,'INSERT')
                             OR has_table_privilege(current_user,c.oid,'UPDATE')
                             OR has_table_privilege(current_user,c.oid,'DELETE')
                             OR has_table_privilege(current_user,c.oid,'TRUNCATE')
                             OR has_table_privilege(current_user,c.oid,'REFERENCES')
                             OR has_table_privilege(current_user,c.oid,'TRIGGER'))
                         ORDER BY c.relname LIMIT 10''')
        writable_relation_sample = [str(row[0]) for row in cursor.fetchall()]
        cursor.execute('''SELECT n.nspname || '.' || c.relname
                          FROM pg_class c
                          JOIN pg_namespace n ON n.oid=c.relnamespace
                         WHERE n.nspname='quant' AND c.relkind='S'
                           AND (has_sequence_privilege(current_user,c.oid,'USAGE')
                             OR has_sequence_privilege(current_user,c.oid,'UPDATE'))
                         ORDER BY c.relname LIMIT 10''')
        writable_sequence_sample = [str(row[0]) for row in cursor.fetchall()]
        cursor.execute('''SELECT n.nspname || '.' || c.relname
                          FROM pg_class c
                          JOIN pg_namespace n ON n.oid=c.relnamespace
                         WHERE n.nspname='quant'
                           AND c.relowner=(SELECT oid FROM pg_roles WHERE rolname=current_user)
                         ORDER BY c.relname LIMIT 10''')
        owned_object_sample = [str(row[0]) for row in cursor.fetchall()]
print(json.dumps({
    "database": database,
    "role": role,
    "database_present": bool(database),
    "canonical_present": bool(canonical_present),
    "alembic_version_present": bool(alembic_present),
    "alembic_version": versions[-1] if versions else None,
    "role_superuser": bool(role_superuser),
    "role_createdb": bool(role_createdb),
    "role_createrole": bool(role_createrole),
    "role_replication": bool(role_replication),
    "role_bypassrls": bool(role_bypassrls),
    "role_inherit": bool(role_inherit),
    "statement_timeout_ms": int(statement_timeout_ms),
    "idle_transaction_timeout_ms": int(idle_transaction_timeout_ms),
    "role_membership_count": int(role_membership_count),
    "role_membership_sample": role_membership_sample,
    "database_owned": bool(database_owned),
    "schema_owned": bool(schema_owned),
    "quant_objects_owned": bool(quant_objects_owned),
    "quant_owned_object_count": int(quant_owned_object_count),
    "quant_owned_object_sample": owned_object_sample,
    "quant_security_definer_executable_count": int(quant_security_definer_executable_count),
    "quant_writable_relation_count": int(quant_writable_relation_count),
    "quant_writable_relation_sample": writable_relation_sample,
    "quant_writable_sequence_count": int(quant_writable_sequence_count),
    "quant_writable_sequence_sample": writable_sequence_sample,
    "schema_creatable": bool(schema_creatable),
    "table_insertable": bool(table_insertable),
    "table_updatable": bool(table_updatable),
    "table_deletable": bool(table_deletable),
    "tunnel_5432": ports.get(5432, False),
    "tunnel_5433": ports.get(5433, False),
}))
"""
        result = self.command(["docker", "exec", container, "python", "-c", "import json\n" + program], timeout=15)
        if result.returncode != 0:
            return {
                "ok": False,
                "error": (result.stderr or result.stdout).strip()[-400:],
            }
        try:
            receipt = json.loads(result.stdout)
        except json.JSONDecodeError as error:
            return {"ok": False, "error": f"invalid readback receipt: {error}"}
        required = batch_database_requirements(receipt)
        return {"ok": all(required.values()), "requirements": required, **receipt}

    def owner_contract(self) -> dict[str, Any]:
        """Read the owner's live contract through the peer's gateway lane."""
        container = self.service_container("quant-research")
        if not container:
            return {"ok": False, "error": "quant_container_missing"}
        program = r'''
import json, os, requests
from app.owner_peer_contract import validate_contract
url = os.environ["QUANT_SHARED_READ_API_BASE_URL"].rstrip("/") + "/api/v1/peer/contract"
response = requests.get(url, headers={"X-Quant-Read-Key": os.environ["QUANT_SHARED_READ_API_KEY"]}, timeout=10)
response.raise_for_status()
payload = response.json()
issues = validate_contract(payload)
print(json.dumps({"payload": payload, "issues": issues}, ensure_ascii=False))
'''
        result = self.command(["docker", "exec", container, "python", "-c", program], timeout=20)
        if result.returncode != 0:
            return {"ok": False, "error": (result.stderr or result.stdout).strip()[-400:]}
        try:
            envelope = json.loads(result.stdout)
        except json.JSONDecodeError as error:
            return {"ok": False, "error": f"invalid owner contract receipt: {error}"}
        payload = envelope.get("payload") or {}
        issues = envelope.get("issues") or []
        return {
            "ok": not issues,
            "alembic_head": payload.get("alembic_head"),
            "object_count": len(payload.get("objects") or []),
            "cold_table_count": len((payload.get("cold_tier") or {}).get("tables") or []),
            "factor_semantics": (payload.get("enumerations") or {}).get("factor_semantics") or [],
            "issues": issues,
        }

    def owner_errors(self) -> dict[str, Any]:
        """Read the owner-side error feed without treating old history as a gate."""
        container = self.service_container("quant-research")
        if not container:
            return {"ok": False, "error": "quant_container_missing"}
        program = r'''
import json, os, requests
url = os.environ["QUANT_SHARED_READ_API_BASE_URL"].rstrip("/") + "/api/v1/peer/errors?limit=20"
response = requests.get(url, headers={"X-Quant-Read-Key": os.environ["QUANT_SHARED_READ_API_KEY"]}, timeout=10)
response.raise_for_status()
payload = response.json()
print(json.dumps({"total_entries": payload.get("total_entries", 0), "distinct_problems": payload.get("distinct_problems", 0)}))
'''
        result = self.command(["docker", "exec", container, "python", "-c", program], timeout=20)
        if result.returncode != 0:
            return {"ok": False, "error": (result.stderr or result.stdout).strip()[-400:]}
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as error:
            return {"ok": False, "error": f"invalid owner error receipt: {error}"}
        return {"ok": True, **payload}

    def storage(self) -> dict[str, dict[str, Any]]:
        with urlopen(f"{self.api_base}/api/v1/research/storage-tiers", timeout=15) as response:
            payload = json.load(response)
        checks = storage_checks(payload)
        lineage = payload.get("database_lineage") or {}
        checks["api_database_lineage"] = {
            "ok": lineage.get("status") == "ready"
                  and bool(lineage.get("database_name"))
                  and bool(lineage.get("alembic_version")),
            "database_name": lineage.get("database_name"),
            "alembic_version": lineage.get("alembic_version"),
            "status": lineage.get("status"),
            "reason": lineage.get("reason"),
        }
        return checks

    def api_build(self) -> dict[str, Any]:
        """Require non-placeholder build provenance before accepting a release."""
        try:
            with urlopen(f"{self.api_base}/health", timeout=15) as response:
                payload = json.load(response)
        except Exception as error:  # operator receipt, not an application exception boundary
            return {"ok": False, "error": f"{type(error).__name__}: {error}"}
        build = payload.get("build") or {}
        release = str(build.get("release") or "").strip()
        expected_ok = self.expected_release is None or release == self.expected_release
        return {
            "ok": payload.get("status") == "ok" and bool(release) and release.lower() not in {"unknown", "unset"} and expected_ok,
            "status": payload.get("status"),
            "release": release or None,
            "git_sha": build.get("git_sha"),
            "build_created_at": build.get("build_created_at"),
            "expected_release": self.expected_release,
        }

    def replay_readiness_contract(self) -> dict[str, Any]:
        """Reject a peer image whose replay gate still counts raw/identity bars."""
        try:
            with urlopen(f"{self.api_base}/api/v1/data-readiness/replay", timeout=15) as response:
                payload = json.load(response)
        except Exception as error:  # operator receipt, not an application exception boundary
            return {"ok": False, "error": f"{type(error).__name__}: {error}"}
        definition = str(payload.get("coverage_definition") or "")
        return {
            "ok": definition == EXPECTED_REPLAY_COVERAGE_DEFINITION,
            "status": payload.get("status"),
            "coverage_definition": definition or None,
            "expected_coverage_definition": EXPECTED_REPLAY_COVERAGE_DEFINITION,
            "readiness_query_status": (payload.get("evidence") or {}).get("readiness_query_status"),
        }


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify owner storage and peer batch cutover")
    parser.add_argument("--stage", choices=("lane", "complete"), default="complete")
    parser.add_argument(
        "--compose-dir",
        type=Path,
        default=Path.home() / "trading_hareness" / "deploy" / "shared-peer",
    )
    parser.add_argument("--api-base", default="http://127.0.0.1:15682")
    parser.add_argument("--batch-host", default="127.0.0.1")
    parser.add_argument("--batch-port", type=int, default=15433)
    parser.add_argument("--expected-release", default=os.getenv("PEER_EXPECTED_RELEASE") or None)
    args = parser.parse_args()
    expected_release = (
        args.expected_release
        or os.getenv("PEER_EXPECTED_RELEASE")
        or compose_env_value(args.compose_dir, "PEER_EXPECTED_RELEASE")
    )
    verifier = Verifier(args.compose_dir, args.api_base, args.batch_host, args.batch_port, expected_release)
    checks: dict[str, dict[str, Any]] = {}
    checks["api_build"] = verifier.api_build()
    checks["replay_readiness_contract"] = verifier.replay_readiness_contract()
    checks["semantics_startup_policy"] = verifier.semantics_policy()
    # The old /api/v1/research/storage-tiers projection was never an owner
    # contract and is intentionally not a release gate.  Use the live,
    # read-only machine contract instead.
    checks["owner_contract"] = verifier.owner_contract()
    checks["owner_error_feed"] = verifier.owner_errors()
    # The owner batch reverse is reached through db-tunnel:5433.  Do not make
    # a host-level 15433 listener a prerequisite: the container's two-port
    # readback below is the authoritative peer-side check.
    checks["batch_container"] = verifier.container_health("db-tunnel")
    checks["batch_database_readback"] = verifier.batch_database_readback()
    batch_lineage = checks.get("batch_database_readback") or {}
    contract = checks.get("owner_contract") or {}
    checks["lane_lineage"] = {
        "ok": contract.get("ok") is True
              and batch_lineage.get("ok") is True
              and contract.get("alembic_head") == batch_lineage.get("alembic_version"),
        "contract_alembic_head": contract.get("alembic_head"),
        "batch_alembic_version": batch_lineage.get("alembic_version"),
    }
    if args.stage == "complete":
        checks["scheduler_lane"] = verifier.scheduler_lane()
    ready = bool(checks) and all(check.get("ok") is True for check in checks.values())
    print(json.dumps({
        "status": "ready" if ready else "blocked",
        "stage": args.stage,
        "checks": checks,
        "read_only": True,
    }, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if ready else 1


if __name__ == "__main__":
    sys.exit(main())
