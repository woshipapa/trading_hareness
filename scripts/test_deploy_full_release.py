"""The full owner release: what it refuses, and the order it does things in.

The remote half needs 47owner, so it is checked by structure; the local gates
run for real against a throwaway repository (no --apply, and RELEASE_DB_REVISION
stands in for the owner's /health, so no ssh).
"""
from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().with_name("shared-peer") / "deploy-full-release.sh"
MIGRATIONS = "quant-service/migrations/versions"


def migration(revision: str, down: str | tuple[str, ...] | None) -> str:
    return f"revision = {revision!r}\ndown_revision = {down!r}\n"


class FullReleaseGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory()
        self.repo = Path(self._directory.name)
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.email", "test@example.invalid")
        self.git("config", "user.name", "test")
        self.base = self.commit({"scripts/shared-peer/activate-peer-release.sh": "#!/bin/sh\n",
                                 f"{MIGRATIONS}/0001_a.py": migration("a", None),
                                 f"{MIGRATIONS}/0002_b.py": migration("b", "a"),
                                 "README.md": "base\n"}, "base without the lock")
        self.with_lock = self.commit({"scripts/shared-peer/release-lock.sh": "#!/bin/sh\n"}, "add the lock")

    def tearDown(self) -> None:
        self._directory.cleanup()

    def git(self, *args: str) -> str:
        return subprocess.run(["git", *args], cwd=self.repo, check=True, capture_output=True, text=True).stdout.strip()

    def commit(self, files: dict[str, str], message: str) -> str:
        for name, text in files.items():
            path = self.repo / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", message)
        return self.git("rev-parse", "HEAD")

    def run_release(self, sha: str, *, clock: str = "4 2300", mainline: str = "main", db_revision: str = "b",
                    allow: str | None = None) -> subprocess.CompletedProcess[str]:
        env = {**os.environ, "RELEASE_CLOCK": clock, "RELEASE_SKIP_FETCH": "1", "RELEASE_MAINLINE": mainline,
               "RELEASE_DB_REVISION": db_revision}
        env.pop("RELEASE_ALLOW_SCHEMA_REVISION", None)
        if allow is not None:
            env["RELEASE_ALLOW_SCHEMA_REVISION"] = allow
        return subprocess.run(["bash", str(SCRIPT), sha, "owner-full-test-1"], cwd=self.repo, env=env,
                              capture_output=True, text=True, timeout=60, check=False)

    def test_a_merged_commit_after_hours_is_a_candidate(self) -> None:
        result = self.run_release(self.with_lock)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"full owner release candidate: sha={self.with_lock}", result.stdout)

    def test_trading_hours_and_the_post_close_scheduler_window_are_refused(self) -> None:
        for clock in ("1 0830", "4 1000", "5 1510", "4 1845", "3 2000", "2 2205"):
            result = self.run_release(self.with_lock, clock=clock)
            self.assertEqual(result.returncode, 3, f"{clock}: {result.stderr}")
            self.assertIn("no-restart window", result.stderr)

    def test_the_edges_of_the_windows_and_weekends_are_allowed(self) -> None:
        for clock in ("4 0829", "4 1511", "4 1844", "4 2206", "6 1000", "7 1900"):
            self.assertEqual(self.run_release(self.with_lock, clock=clock).returncode, 0, clock)

    def test_a_commit_not_on_the_mainline_is_refused(self) -> None:
        self.git("checkout", "-q", "-b", "side")
        side = self.commit({"README.md": "side\n"}, "unmerged work")
        result = self.run_release(side, mainline="main")
        self.assertEqual(result.returncode, 4, result.stderr)
        self.assertIn("not on main", result.stderr)

    def test_a_payload_without_the_guard_lock_is_refused(self) -> None:
        self.git("checkout", "-q", "-b", "old", self.base)
        result = self.run_release(self.base, mainline="old")
        self.assertEqual(result.returncode, 5, result.stderr)
        self.assertIn("predates the guard lock", result.stderr)

    def test_a_database_at_the_release_head_passes_and_is_reported(self) -> None:
        result = self.run_release(self.with_lock)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("schema: owner database b, release head b: at_head", result.stdout)

    def test_a_database_behind_the_release_is_refused_with_what_to_apply_and_no_override(self) -> None:
        ahead = self.commit({f"{MIGRATIONS}/0003_c.py": migration("c", "b")}, "a new migration")
        for allow in (None, "b", "none"):
            result = self.run_release(ahead, allow=allow)
            self.assertEqual(result.returncode, 6, result.stderr)
            self.assertIn("is at b but this release needs c", result.stderr)
            self.assertIn("    c\n", result.stderr)
            self.assertIn("upgrade b:head --sql", result.stderr)

    def test_an_unknown_revision_passes_only_when_named_exactly(self) -> None:
        refused = self.run_release(self.with_lock, db_revision="owner_only_0117")
        self.assertEqual(refused.returncode, 6, refused.stderr)
        self.assertIn("never recreate it from memory", refused.stderr)
        self.assertEqual(self.run_release(self.with_lock, db_revision="owner_only_0117", allow="other").returncode, 6)
        allowed = self.run_release(self.with_lock, db_revision="owner_only_0117", allow="owner_only_0117")
        self.assertEqual(allowed.returncode, 0, allowed.stderr)
        self.assertIn("unknown revision (allowed)", allowed.stdout)

    def test_an_unreadable_revision_needs_the_none_override(self) -> None:
        refused = self.run_release(self.with_lock, db_revision="")
        self.assertEqual(refused.returncode, 6, refused.stderr)
        self.assertIn("RELEASE_ALLOW_SCHEMA_REVISION=none", refused.stderr)
        allowed = self.run_release(self.with_lock, db_revision="", allow="none")
        self.assertEqual(allowed.returncode, 0, allowed.stderr)
        self.assertIn("schema: owner database unreadable, release head b: unverified (allowed)", allowed.stdout)

    def test_two_migration_heads_are_refused(self) -> None:
        forked = self.commit({f"{MIGRATIONS}/0003_c.py": migration("c", "b"),
                              f"{MIGRATIONS}/0003_d.py": migration("d", "b")}, "two heads")
        result = self.run_release(forked)
        self.assertEqual(result.returncode, 6, result.stderr)
        self.assertIn("lineage", result.stderr)

    def test_a_short_sha_or_unsafe_label_is_refused(self) -> None:
        env = {**os.environ, "RELEASE_CLOCK": "4 2300", "RELEASE_SKIP_FETCH": "1"}
        for args in ((self.with_lock[:7], "ok-label"), (self.with_lock, "bad label")):
            result = subprocess.run(["bash", str(SCRIPT), *args], cwd=self.repo, env=env,
                                    capture_output=True, text=True, timeout=30, check=False)
            self.assertEqual(result.returncode, 2, args)


class FullReleaseRemoteOrderTests(unittest.TestCase):
    def setUp(self) -> None:
        script = SCRIPT.read_text(encoding="utf-8")
        self.remote = script[script.index("<<'REMOTE'"):]

    def test_the_rollback_point_is_recorded_before_anything_changes(self) -> None:
        recorded = self.remote.index('"$STATE/$RELEASE_LABEL.rollback"')
        self.assertLess(recorded, self.remote.index('bash "$STATE/activate-$RELEASE_LABEL.sh"'))
        self.assertIn('docker tag "$IMAGE:latest" "$IMAGE:rollback-$RELEASE_LABEL"', self.remote)

    def test_the_guard_lock_is_held_before_any_container_restarts(self) -> None:
        body = self.remote[self.remote.index("# --- activate"):]
        self.assertLess(body.index('bash "$lock_tool" hold "$RELEASE_LABEL"'), body.index("up_in_order"))

    def test_metadata_goes_through_the_symlink(self) -> None:
        self.assertIn('env_file="$(readlink -f "$compose_dir/.env")"', self.remote)
        self.assertIn('[ -L "$compose_dir/.env" ] ||', self.remote)

    def test_the_main_service_is_healthy_before_the_scheduler_and_the_tunnel_is_not_recreated(self) -> None:
        start = self.remote.index("up_in_order() {")
        body = self.remote[start:self.remote.index("\n}\n", start)]
        tunnel = body.index("--wait db-tunnel")
        main = body.index("--force-recreate --wait quant-research \\")
        scheduler = body.index("--force-recreate --wait quant-research-scheduler")
        self.assertLess(tunnel, main)
        self.assertLess(main, scheduler)
        self.assertNotIn("--force-recreate --wait db-tunnel", body)

    def test_a_failure_rolls_back_and_always_releases_the_lock(self) -> None:
        finish = self.remote[self.remote.index("finish() {"):]
        finish = finish[:finish.index("\n}\n")]
        self.assertIn("roll_back", finish)
        self.assertIn('release "$RELEASE_LABEL"', finish)
        self.assertIn("trap finish EXIT", self.remote)
        rollback = self.remote[self.remote.index("roll_back() {"):]
        rollback = rollback[:rollback.index("\n}\n")]
        for step in ('"$HOME/trading_hareness"', '"$HOME/wheelhouse"', "write_meta $prev_meta",
                     '"$IMAGE:rollback-$RELEASE_LABEL" "$IMAGE:latest"', "up_in_order"):
            self.assertIn(step, rollback)


if __name__ == "__main__":
    unittest.main()
