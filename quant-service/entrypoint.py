"""Run versioned schema migrations safely before starting the API process."""

from __future__ import annotations

import os
import subprocess
import sys
import time

import psycopg


# This is a fixed application-level lock namespace, not derived from a secret.
# Every quant-research instance must hold it while applying Alembic revisions.
MIGRATION_ADVISORY_LOCK_KEY = 7_265_811_000_001


def migration_lock_timeout_seconds() -> int:
    raw = os.getenv("QUANT_MIGRATION_LOCK_TIMEOUT_SECONDS", "60")
    try:
        return max(1, min(int(raw), 600))
    except ValueError:
        return 60


def database_connection() -> psycopg.Connection:
    return psycopg.connect(
        host=os.getenv("PGHOST", "postgres"),
        port=os.getenv("PGPORT", "5432"),
        dbname=os.getenv("PGDATABASE", "n8n"),
        user=os.getenv("PGUSER", "n8n"),
        password=os.getenv("PGPASSWORD", ""),
        connect_timeout=10,
        application_name=os.getenv("QUANT_APPLICATION_NAME", "quant-entrypoint"),
        autocommit=True,
    )


def acquire_migration_lock(connection: psycopg.Connection) -> None:
    deadline = time.monotonic() + migration_lock_timeout_seconds()
    while True:
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_try_advisory_lock(%s)", (MIGRATION_ADVISORY_LOCK_KEY,))
            acquired = bool(cursor.fetchone()[0])
        if acquired:
            return
        if time.monotonic() >= deadline:
            raise RuntimeError("timed out waiting for the quant schema migration lock")
        time.sleep(1)


def release_migration_lock(connection: psycopg.Connection) -> None:
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_unlock(%s)", (MIGRATION_ADVISORY_LOCK_KEY,))


def migration_command() -> list[str]:
    """Run Alembic from the same virtual environment as the service."""
    return [sys.executable, "-m", "alembic", "-c", "alembic.ini", "upgrade", "head"]


def migrations_enabled() -> bool:
    """Allow read-only peer profiles to start without owner DB DDL access."""
    return os.getenv("QUANT_SKIP_MIGRATIONS", "false").strip().lower() not in {
        "1", "true", "yes", "on",
    }


def owner_cutover_required() -> bool:
    """Require the owner layout whenever the scheduler is placed on 5433."""
    configured = os.getenv("PEER_REQUIRE_OWNER_CUTOVER")
    if configured is not None and configured.strip().lower() not in {"", "auto"}:
        return configured.strip().lower() in {"1", "true", "yes", "on"}
    return (
        os.getenv("PGHOST", "").strip() == "db-batch-tunnel"
        or os.getenv("PGPORT", "5432").strip() == "5433"
    )


def owner_semantics_required() -> bool:
    """Require the owner adjustment contract before new peer SQL can start."""
    configured = os.getenv("PEER_REQUIRE_OWNER_SEMANTICS")
    if configured is None:
        return False
    return configured.strip().lower() in {"1", "true", "yes", "on"}


def validate_owner_cutover_status(status: dict[str, object]) -> None:
    """Reject a batch scheduler that cannot read the owner runtime schema."""
    compatible = (
        status.get("status") == "owner_compatible" and status.get("schema_ready") is True
    ) or (
        # Keep accepting the old in-process diagnostic shape for callers that
        # only exercise the validator; production verification now passes the
        # owner_runtime_schema_status shape above.
        status.get("status") == "layered" and status.get("cutover_ready") is True
    )
    if not compatible:
        issues = status.get("issues") or ["owner_runtime_schema_not_ready"]
        raise RuntimeError(
            "owner runtime schema is not compatible; refusing batch scheduler startup: "
            + ", ".join(str(issue) for issue in issues)
        )


def validate_owner_semantics_status(status: dict[str, object]) -> None:
    """Validate only the owner runtime projection, never proposed schema.

    Owner v2 keeps factor semantics in ``raw`` and has no guard indexes.  The
    machine-readable contract is the startup authority; this compatibility
    validator accepts its non-blocking diagnostic shape for older callers.
    """
    semantics = status.get("adjustment_semantics") or {}
    guard_status = (semantics.get("guard_indexes") or {}).get("status")
    data_guard_status = (semantics.get("data_guard") or {}).get("status")
    contract_ready = (
        semantics.get("ready") is True
        and guard_status in {None, "ready", "not_applicable"}
        and data_guard_status in {None, "ready", "not_applicable"}
    )
    compatible = (
        status.get("status") == "owner_compatible" and status.get("schema_ready") is True
    ) or contract_ready
    if not compatible:
        issues = status.get("issues") or ["owner_runtime_schema_not_ready"]
        raise RuntimeError(
            "owner runtime schema is not compatible; refusing peer startup: "
            + ", ".join(str(issue) for issue in issues)
        )


def verify_owner_semantics() -> None:
    """Read the live owner contract before starting an owner-bound peer."""
    if not owner_semantics_required():
        return
    from app.owner_peer_contract import verify_owner_peer_contract

    verify_owner_peer_contract(required=True)
    print("reported owner peer contract; peer SQL may start", flush=True)


def verify_owner_cutover() -> None:
    """Read the live owner contract before starting a 5433-bound process."""
    if not owner_cutover_required():
        return
    if os.getenv("PGHOST", "").strip() != "db-tunnel" or os.getenv("PGPORT", "").strip() != "5433":
        raise RuntimeError("owner cutover requires PGHOST=db-tunnel and PGPORT=5433")
    from app.owner_peer_contract import verify_owner_peer_contract

    verify_owner_peer_contract(required=True)
    print("reported owner peer contract; batch scheduler may start", flush=True)


def main(argv: list[str]) -> None:
    if not argv:
        raise SystemExit("usage: entrypoint.py <service command>")

    # Both flags describe the same owner contract. Check it once per process
    # so a report-only receipt written by the first function cannot
    # accidentally make a first-ever blocking startup pass in the same boot.
    verify_owner_semantics()
    if not owner_semantics_required():
        verify_owner_cutover()
    if migrations_enabled():
        connection = database_connection()
        try:
            acquire_migration_lock(connection)
            print("applying versioned quant schema migrations", flush=True)
            subprocess.run(migration_command(), check=True)
        finally:
            try:
                release_migration_lock(connection)
            finally:
                connection.close()
    else:
        print("skipping quant schema migrations (read-only peer profile)", flush=True)

    os.execvp(argv[0], argv)


if __name__ == "__main__":
    main(sys.argv[1:])
