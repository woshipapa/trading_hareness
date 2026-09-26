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


if __name__ == "__main__":
    unittest.main()
