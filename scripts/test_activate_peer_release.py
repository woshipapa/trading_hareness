import hashlib
import io
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import unittest


SCRIPT = Path(__file__).with_name("shared-peer") / "activate-peer-release.sh"
TUNNEL_SCRIPTS = (
    SCRIPT.with_name("start-shared-tunnels.ps1"),
    SCRIPT.with_name("start-shared-peer-batch-tunnel.ps1"),
    SCRIPT.with_name("verify-shared-runtime.ps1"),
)


class ActivatePeerReleaseTests(unittest.TestCase):
    def test_source_packager_is_present_and_checks_runtime_assets(self) -> None:
        packager = SCRIPT.with_name("package-peer-release.sh")
        source = packager.read_text(encoding="utf-8")
        self.assertIn("scripts/peer-session-guard.sh", source)
        self.assertIn("scripts/backfill-full-market-daily.sh", source)
        self.assertIn("OPERATIONS.md", source)
        self.assertIn("tar -czf", source)

    def test_release_validation_requires_two_lane_runtime_files(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertIn('compose.intraday-owner.yaml', source)
        self.assertIn('scripts/peer-session-guard.sh', source)
        self.assertIn('scripts/backfill-full-market-daily.sh', source)
        self.assertIn('OPERATIONS.md', source)
        self.assertIn('frontend', source)
        self.assertIn('workflows', source)
        self.assertIn('certs', source)
        self.assertIn("grep -q 'PEER_REQUIRE_OWNER_SEMANTICS'", source)
        self.assertIn("grep -q 'owner_semantics_required'", source)
        self.assertIn("owner_peer_contract.py", source)
        self.assertIn("api/v1/peer/contract", source)
        self.assertIn('ssh-tunnel-entrypoint.sh', source)
        self.assertIn("grep -q 'LOCAL_DB_BIND_PORT'", source)
        self.assertIn("5433:127.0.0.1:15433", source)
        self.assertIn('ensure-batch-tunnel.sh', source)
        self.assertIn('verify-owner-cutover.py', source)
        self.assertIn("grep -q 'no_security_definer_functions'", source)
        self.assertIn("grep -q 'role_membership_observed'", source)
        self.assertIn("grep -q 'write_capabilities_observed'", source)
        self.assertIn('bootstrap-local-peer.ps1', source)
        self.assertIn("grep -q '\\[switch\\]\\$DryRun'", source)
        self.assertIn("REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA quant", source)
        self.assertIn("REVOKE ALL PRIVILEGES ON SCHEMA quant", source)
        self.assertIn("REVOKE ALL PRIVILEGES ON ALL FUNCTIONS IN SCHEMA quant", source)
        self.assertIn("REVOKE ALL PRIVILEGES ON ALL PROCEDURES IN SCHEMA quant", source)
        self.assertIn("NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS NOINHERIT", source)
        self.assertIn("CREATE ROLE \\$PeerRole LOGIN NOINHERIT", source)
        self.assertIn("ALTER ROLE \\$PeerRole LOGIN NOINHERIT", source)
        self.assertIn("statement_timeout = '15min'", source)
        self.assertIn("idle_in_transaction_session_timeout = '5min'", source)
        self.assertIn("REVOKE %I FROM %I", source)
        self.assertIn("WritableQuantRelationCount", source)
        self.assertIn("WritableQuantSequenceCount", source)
        self.assertIn("REASSIGN OWNED BY $PeerRole TO $($runtime.PGADMINUSER)", source)
        self.assertIn("ALTER DATABASE $PeerN8nDatabase OWNER TO $PeerRole", source)
        self.assertIn("unexpected shared objects", source)
        self.assertIn("pg_get_userbyid(datdba)", source)
        self.assertIn("ALTER DATABASE $($runtime.PGDATABASE) OWNER TO $($runtime.PGADMINUSER)", source)
        self.assertIn("pg_get_userbyid(nspowner)", source)
        self.assertIn("ALTER SCHEMA quant OWNER TO $($runtime.PGADMINUSER)", source)
        self.assertIn("has_schema_privilege('\\$PeerRole','quant','CREATE')", source)
        self.assertIn("rolinherit FROM pg_roles", source)
        self.assertIn("datdba", source)
        self.assertIn("nspowner", source)
        self.assertIn("c.relowner", source)
        self.assertIn('quant-service/app/owner_storage.py', source)
        self.assertIn("OWNER_RUNTIME_REQUIRED_COLUMNS", source)
        self.assertIn("canonical_bars_daily", source)
        self.assertIn("daily_adjustment_factors", source)
        self.assertIn("persisted_factor_semantics_sql", source)
        self.assertIn("raw->>'factor_semantics'", source)
        self.assertIn('quant-service/app/event_research.py', source)
        self.assertIn("persisted_adj_factor", source)
        self.assertIn("daily_adjustment_factors", source)
        self.assertIn('owner_factor_repository.py', source)
        self.assertIn("owner_persisted_adjustment_factor", source)
        self.assertIn("full_market_daily_controls_sync.py", source)
        self.assertIn("core_daily_control_sync.py", source)
        self.assertIn("stock_study_service.py", source)
        self.assertIn('quant-service/app/main.py', source)
        self.assertIn('read_persisted_factor_window', source)
        self.assertIn('post_close_strategy_service.py', source)
        self.assertIn('quant-service/app/instrument_registry.py', source)
        self.assertIn('quant-service/app/instrument_lock_retry.py', source)
        self.assertIn("ORDER BY symbol", source)
        self.assertIn('quant-service/app/replay_readiness_coverage.py', source)
        self.assertIn("grep -q 'complete_adjusted'", source)
        self.assertIn("EXPECTED_REPLAY_COVERAGE_DEFINITION", source)
        self.assertIn('set_env_if_placeholder PEER_APP_RELEASE', source)
        self.assertIn('set_env_if_placeholder PEER_EXPECTED_RELEASE', source)
        self.assertIn('app_release=', source)
        self.assertIn('APP_BUILD_CREATED_AT', source)
        self.assertIn('saved_intraday_secrets=', source)
        self.assertIn('deploy/shared-peer/intraday-secrets.env', source)
        self.assertIn('"${saved_intraday_secrets}"', source)

    def test_scheduler_release_carries_explicit_owner_cutover_policy(self) -> None:
        compose = (SCRIPT.parents[2] / "deploy" / "shared-peer" / "compose.intraday-owner.yaml").read_text(encoding="utf-8")
        self.assertIn("dockerfile: quant-service/Dockerfile.peer", compose)
        self.assertIn("PEER_QUANT_IMAGE", compose)
        self.assertIn("PEER_REQUIRE_OWNER_CUTOVER", compose)
        self.assertIn("${PEER_REQUIRE_OWNER_CUTOVER:-auto}", compose)
        self.assertIn("PEER_REQUIRE_OWNER_SEMANTICS", compose)
        base = (SCRIPT.parents[2] / "deploy" / "shared-peer" / "compose.yaml").read_text(encoding="utf-8")
        self.assertIn("image: ${PEER_QUANT_IMAGE:-trading-hareness-peer-quant-research:latest}", base)
        self.assertIn("PEER_REQUIRE_OWNER_SEMANTICS", base)

    def test_tunnel_scripts_accept_direct_host_port_user_and_key(self) -> None:
        for path in TUNNEL_SCRIPTS:
            source = path.read_text(encoding="utf-8")
            self.assertIn("SshHost", source)
            self.assertIn("SshPort", source)
            self.assertIn("SshUser", source)
            self.assertIn("SshKeyPath", source)
            self.assertIn("KnownHostsPath", source)
            self.assertIn("IdentitiesOnly=yes", source)
            self.assertIn("StrictHostKeyChecking=yes", source)
            self.assertIn("PEER_SSH_HOST", source)


def _gnu_userland() -> bool:
    """The activation script runs on the Linux owner and uses GNU mv/sed."""
    try:
        version = subprocess.run(["mv", "--version"], capture_output=True, text=True, check=False).stdout
    except OSError:
        return False
    return "GNU" in version and shutil.which("sha256sum") is not None


def _repo_archive(destination: Path) -> bool:
    """A real release payload: the tracked tree plus the empty certs dir git cannot carry."""
    prebuilt = os.environ.get("ACTIVATION_REPO_ARCHIVE")
    if prebuilt:
        shutil.copyfile(prebuilt, destination)
    elif shutil.which("git"):
        with destination.open("wb") as handle:
            subprocess.run(["git", "-C", str(SCRIPT.parents[2]), "archive", "--format=tar",
                            "--prefix=trading_hareness/", "HEAD"], stdout=handle, check=True)
    else:
        return False
    with tarfile.open(destination, "a") as archive:
        certs = tarfile.TarInfo("trading_hareness/certs")
        certs.type, certs.mode = tarfile.DIRTYPE, 0o755
        archive.addfile(certs)
    return True


@unittest.skipUnless(_gnu_userland(), "the activation script needs the owner's GNU userland")
class ActivationKeepsCentralCredentialsTests(unittest.TestCase):
    def setUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory()
        self.home = Path(self._directory.name)
        self.repo_archive = self.home / "repo.tar"
        if not _repo_archive(self.repo_archive):
            self.skipTest("needs git or ACTIVATION_REPO_ARCHIVE to build a release payload")
        payload = b"wheel"
        self.wheelhouse_archive = self.home / "wheelhouse.tar"
        with tarfile.open(self.wheelhouse_archive, "w") as archive:
            for name, data in (
                ("wheelhouse/pkg.whl", payload),
                ("wheelhouse/SHA256SUMS", f"{hashlib.sha256(payload).hexdigest()}  pkg.whl\n".encode()),
            ):
                info = tarfile.TarInfo(name)
                info.size = len(data)
                archive.addfile(info, io.BytesIO(data))
        previous = self.home / "releases" / "previous" / "trading_hareness" / "deploy" / "shared-peer"
        previous.mkdir(parents=True)
        (self.home / "trading_hareness").symlink_to(previous.parents[1])
        self.previous_env_dir = previous

    def tearDown(self) -> None:
        self._directory.cleanup()

    def activate(self) -> Path:
        env = {**os.environ, "PEER_HOME": str(self.home), "PEER_RELEASES_ROOT": str(self.home / "releases"),
               "RELEASE_ID": "next"}
        result = subprocess.run(["bash", str(SCRIPT), str(self.repo_archive), str(self.wheelhouse_archive)],
                                env=env, capture_output=True, text=True, timeout=120, check=False)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        active = (self.home / "trading_hareness").resolve()
        self.assertEqual(active, (self.home / "releases" / "next" / "trading_hareness").resolve())
        return active / "deploy" / "shared-peer"

    def test_symlinks_into_the_central_store_survive_activation(self) -> None:
        central = self.home / ".secrets" / "owner" / ".env.owner"
        central.parent.mkdir(parents=True)
        central.write_text("PEER_APP_RELEASE=previous-release\r\nPEER_APP_GIT_SHA=abc\r\n", encoding="utf-8")
        for name in (".env", "intraday-secrets.env"):
            (self.previous_env_dir / name).symlink_to(central)

        env_dir = self.activate()

        for name in (".env", "intraday-secrets.env"):
            path = env_dir / name
            self.assertTrue(path.is_symlink(), f"{name} became a regular file")
            self.assertEqual(path.resolve(), central.resolve())
        content = central.read_text(encoding="utf-8")
        self.assertNotIn("\r", content, "carriage returns must be stripped in the real file")
        self.assertIn("PEER_APP_RELEASE=previous-release", content, "a real value is not a placeholder")
        self.assertTrue(central.is_file() and not central.is_symlink())

    def test_plain_files_are_still_copied_and_placeholders_filled(self) -> None:
        (self.previous_env_dir / ".env").write_text("PEER_APP_RELEASE=unknown\nKEEP=1\n", encoding="utf-8")
        (self.previous_env_dir / "intraday-secrets.env").write_text("TOKEN=x\r\n", encoding="utf-8")

        env_dir = self.activate()

        env_file = env_dir / ".env"
        self.assertFalse(env_file.is_symlink())
        self.assertEqual(oct(env_file.stat().st_mode & 0o777), "0o600")
        content = env_file.read_text(encoding="utf-8")
        self.assertIn("PEER_APP_RELEASE=next", content)
        self.assertIn("KEEP=1", content)
        self.assertEqual((env_dir / "intraday-secrets.env").read_text(encoding="utf-8"), "TOKEN=x\n")


if __name__ == "__main__":
    unittest.main()
