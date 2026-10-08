from __future__ import annotations

import os
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GUARD = ROOT / "scripts" / "peer-session-guard.sh"


class PeerSessionGuardBatchTunnelTests(unittest.TestCase):
    def _run_guard(
        self,
        *,
        scheduler_host: str = "db-tunnel",
        scheduler_port: str = "5432",
        batch_state: str = "missing",
        required: str = "auto",
    ) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fake_bin = root / "bin"
            fake_bin.mkdir()
            docker = fake_bin / "docker"
            docker.write_text(
                textwrap.dedent(
                    """\
                    #!/usr/bin/env bash
                    set -eu
                    if [ "${1:-}" != inspect ]; then
                      echo "unexpected docker command: $*" >&2
                      exit 90
                    fi
                    target=""
                    for arg in "$@"; do
                      case "$arg" in trading-hareness-peer-*) target="$arg" ;; esac
                    done
                    if [[ "$target" == *db-batch-tunnel* ]] && [ "${FAKE_BATCH_STATE:-missing}" = missing ]; then
                      echo "error: no such object: $target"
                      exit 1
                    fi
                    if [[ "$*" == *Config.Env* ]]; then
                      printf 'PGHOST=%s\nPGPORT=%s\n' "${FAKE_SCHEDULER_HOST:-db-tunnel}" "${FAKE_SCHEDULER_PORT:-5432}"
                    elif [[ "$*" == *State.Health* ]]; then
                      echo healthy
                    elif [[ "$*" == *State.Status* ]]; then
                      echo running
                    else
                      echo "unexpected inspect: $*" >&2
                      exit 91
                    fi
                    """
                ),
                encoding="utf-8",
            )
            docker.chmod(0o755)
            curl = fake_bin / "curl"
            curl.write_text(
                "#!/usr/bin/env bash\nprintf '%s\\n' '{\"async_database_pool\":{\"pool_size\":1,\"waiting\":0}}'\n",
                encoding="utf-8",
            )
            curl.chmod(0o755)
            date = fake_bin / "date"
            date.write_text(
                "#!/usr/bin/env bash\nif [ \"${1:-}\" = '+%s' ]; then echo 1789790000; else echo '2026-09-19T12:00:00+08:00'; fi\n",
                encoding="utf-8",
            )
            date.chmod(0o755)
            compose_dir = root / "compose"
            compose_dir.mkdir()
            (compose_dir / "compose.intraday-owner.yaml").write_text("services: {}\n", encoding="utf-8")
            env = {
                **os.environ,
                "PATH": f"{fake_bin}:{os.environ['PATH']}",
                "HOME": str(root),
                "PEER_COMPOSE_DIR": str(compose_dir),
                "PEER_GUARD_STATE_DIR": str(root / "state"),
                "PEER_BATCH_TUNNEL_REQUIRED": required,
                "FAKE_BATCH_STATE": batch_state,
                "FAKE_SCHEDULER_HOST": scheduler_host,
                "FAKE_SCHEDULER_PORT": scheduler_port,
            }
            return subprocess.run(
                ["bash", str(GUARD), "heal"],
                env=env,
                text=True,
                capture_output=True,
                timeout=10,
                check=False,
            )

    def test_auto_keeps_missing_batch_tunnel_optional_on_legacy_scheduler_lane(self) -> None:
        result = self._run_guard()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("db-batch-tunnel", result.stdout)

    def test_auto_keeps_compatibility_sidecar_optional_on_new_5433_scheduler(self) -> None:
        result = self._run_guard(scheduler_host="db-tunnel", scheduler_port="5433")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("db-batch-tunnel", result.stdout)

    def test_explicit_false_supports_a_deliberately_disabled_batch_lane(self) -> None:
        result = self._run_guard(
            scheduler_host="db-batch-tunnel",
            scheduler_port="5433",
            required="false",
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_invalid_requirement_fails_closed(self) -> None:
        result = self._run_guard(required="sometimes")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("must be auto/true/false", result.stdout)


LOCK_TOOL = ROOT / "scripts" / "shared-peer" / "release-lock.sh"
FIXED_EPOCH = 1789790000
ALERT_KEYS = ("PEER_GUARD_FEISHU_WEBHOOK", "FEISHU_APP_ID", "FEISHU_APP_SECRET", "FEISHU_ALERT_RECEIVE_ID")


def _fake_tools(root: Path, *, health: str) -> dict[str, str]:
    """Fake docker/curl/date; every docker call other than inspect is recorded and refused."""
    fake_bin = root / "bin"
    fake_bin.mkdir()
    (fake_bin / "docker").write_text(textwrap.dedent(f"""\
        #!/usr/bin/env bash
        echo "$*" >> "{root}/docker-calls"
        if [ "${{1:-}}" != inspect ]; then exit 90; fi
        if [[ "$*" == *State.Health* ]]; then echo {health}
        elif [[ "$*" == *State.Status* ]]; then echo running
        elif [[ "$*" == *Config.Env* ]]; then echo QUANT_RUNTIME_PROFILE=intraday_edge
        else exit 91; fi
        """), encoding="utf-8")
    (fake_bin / "curl").write_text(
        "#!/usr/bin/env bash\nprintf '%s\\n' '{\"async_database_pool\":{\"pool_size\":1,\"waiting\":0}}'\n",
        encoding="utf-8",
    )
    (fake_bin / "date").write_text(
        f"#!/usr/bin/env bash\nif [ \"${{1:-}}\" = '+%s' ]; then echo {FIXED_EPOCH}; "
        "else echo '2026-10-08T23:00:00+08:00'; fi\n",
        encoding="utf-8",
    )
    for tool in fake_bin.iterdir():
        tool.chmod(0o755)
    compose_dir = root / "compose"
    compose_dir.mkdir()
    (compose_dir / "compose.intraday-owner.yaml").write_text("services: {}\n", encoding="utf-8")
    env = {key: value for key, value in os.environ.items() if key not in ALERT_KEYS}
    env.update({
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "HOME": str(root),
        "PEER_COMPOSE_DIR": str(compose_dir),
        "PEER_GUARD_STATE_DIR": str(root / "state"),
    })
    return env


class ReleaseLockToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory()
        self.root = Path(self._directory.name)
        self.env = _fake_tools(self.root, health="healthy")
        self.lock = self.root / "state" / "release.lock"

    def tearDown(self) -> None:
        self._directory.cleanup()

    def lock_tool(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(["bash", str(LOCK_TOOL), *args], env=self.env, text=True,
                              capture_output=True, timeout=10, check=False)

    def test_hold_status_release_round_trip(self) -> None:
        self.assertEqual(self.lock_tool("status").stdout.strip(), "free")
        self.assertEqual(self.lock_tool("hold", "rel-1", "60").returncode, 0)
        self.assertEqual(self.lock_tool("status").stdout.strip(), f"held rel-1 {FIXED_EPOCH + 60}")
        self.assertEqual(self.lock_tool("release", "rel-1").returncode, 0)
        self.assertFalse(self.lock.exists())

    def test_a_second_release_cannot_take_a_live_lock(self) -> None:
        self.lock_tool("hold", "rel-1", "60")
        result = self.lock_tool("hold", "rel-2", "60")
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("held by rel-1", result.stderr)
        self.assertEqual(self.lock_tool("release", "rel-2").returncode, 2)
        self.assertTrue(self.lock.exists(), "another release must not drop a lock it does not own")

    def test_an_expired_lock_reads_as_stale_and_can_be_taken_over(self) -> None:
        self.lock.parent.mkdir(parents=True)
        self.lock.write_text(f"{FIXED_EPOCH - 1} crashed-release\n", encoding="utf-8")
        self.assertEqual(self.lock_tool("status").stdout.strip(), f"stale crashed-release {FIXED_EPOCH - 1}")
        self.assertEqual(self.lock_tool("hold", "rel-2", "60").returncode, 0)

    def test_rejects_unsafe_labels_and_unbounded_ttl(self) -> None:
        self.assertEqual(self.lock_tool("hold", "a b").returncode, 64)
        self.assertEqual(self.lock_tool("hold", "rel", "99999").returncode, 64)


class GuardHonoursTheReleaseLockTests(unittest.TestCase):
    def run_guard(self, mode: str, *, health: str, lock: str | None) -> tuple[subprocess.CompletedProcess[str], str, bool]:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = _fake_tools(root, health=health)
            if lock is not None:
                state = root / "state"
                state.mkdir()
                (state / "release.lock").write_text(lock, encoding="utf-8")
            result = subprocess.run(["bash", str(GUARD), mode], env=env, text=True,
                                    capture_output=True, timeout=20, check=False)
            calls_file = root / "docker-calls"
            calls = calls_file.read_text(encoding="utf-8") if calls_file.exists() else ""
            return result, calls, (root / "state" / "release.lock").exists()

    def test_heal_does_nothing_while_a_release_holds_the_lock(self) -> None:
        result, calls, still_locked = self.run_guard(
            "heal", health="unhealthy", lock=f"{FIXED_EPOCH + 600} owner-code-1\n")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("release owner-code-1 holds the guard lock", result.stdout)
        self.assertNotIn("compose", calls, "the guard restarted a container during a release")
        self.assertNotIn("PROBLEM", result.stdout)
        self.assertTrue(still_locked)

    def test_preopen_reports_the_lock_and_still_does_not_restart(self) -> None:
        result, calls, _ = self.run_guard(
            "preopen", health="unhealthy", lock=f"{FIXED_EPOCH + 600} owner-code-1\n")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("still holds the guard lock during preopen", result.stdout)
        self.assertIn("not restarting while it runs", result.stdout)
        self.assertNotIn("compose", calls)

    def test_a_stale_lock_is_removed_and_healing_resumes(self) -> None:
        result, calls, still_locked = self.run_guard(
            "heal", health="unhealthy", lock=f"{FIXED_EPOCH - 5} crashed-release\n")
        self.assertIn("expired", result.stdout)
        self.assertFalse(still_locked)
        self.assertIn("compose", calls, "a crashed release must not silence the guard past its TTL")

    def test_no_lock_means_normal_healing(self) -> None:
        result, calls, _ = self.run_guard("heal", health="healthy", lock=None)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("compose", calls)


class CodeOnlyReleaseTakesTheLockTests(unittest.TestCase):
    def test_the_remote_switch_holds_the_guard_lock_before_touching_current(self) -> None:
        script = (ROOT / "scripts" / "shared-peer" / "deploy-code-only.sh").read_text(encoding="utf-8")
        remote = script[script.index("<<'REMOTE'"):]
        hold = remote.index('"$lock_tool" hold "$RELEASE_LABEL"')
        self.assertLess(hold, remote.index('mv -Tf "${current_root}.next" "$current_root"'))
        self.assertIn('"$lock_tool" release "$RELEASE_LABEL"', remote[:remote.index("release_root=")])


if __name__ == "__main__":
    unittest.main()
