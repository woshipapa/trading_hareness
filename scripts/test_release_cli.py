"""The release entry point and its read-only planner, against a throwaway repository."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ("scripts/release_plan.py", "scripts/release_window.py", "scripts/shared-peer/classify-owner-paths.sh",
         "config/release-windows.json", "quant-service/scripts/migration_lineage.py")
MIGRATIONS = "quant-service/migrations/versions"


class ReleasePlanTests(unittest.TestCase):
    def setUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory()
        self.repo = Path(self._directory.name)
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.email", "test@example.invalid")
        self.git("config", "user.name", "test")
        for tool in TOOLS:
            (self.repo / tool).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / tool, self.repo / tool)
        self.base = self.commit({"quant-service/app/main.py": "base\n", "feishu-relay/adapter/index.mjs": "base\n",
                                 f"{MIGRATIONS}/0001_a.py": "revision = 'a'\ndown_revision = None\n"}, "base")

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

    def plan(self, target: str, owner_from: str, edge_from: str, db: str = "a", clock: str = "6 1000") -> str:
        env = {**os.environ, "RELEASE_CLOCK": clock}
        result = subprocess.run([sys.executable, str(self.repo / "scripts/release_plan.py"), target,
                                 "--owner-from", owner_from, "--edge-from", edge_from, "--owner-db", db],
                                cwd=self.repo, env=env, capture_output=True, text=True, check=True)
        return result.stdout

    def test_runtime_only_changes_are_a_code_only_overlay(self) -> None:
        target = self.commit({"quant-service/app/main.py": "changed\n"}, "app change")
        out = self.plan(target, self.base, target)
        self.assertIn("owner: code-only overlay (1 runtime file(s))", out)
        self.assertIn(f"release owner code {target} owner-<label> --from-sha {self.base} --apply", out)
        self.assertIn("edge: already at", out)

    def test_a_dependency_change_needs_a_full_release_and_says_why(self) -> None:
        target = self.commit({"quant-service/requirements.txt": "x==1\n"}, "dependency")
        out = self.plan(target, self.base, self.base)
        self.assertIn("owner: FULL release (1 path(s) need it, e.g. quant-service/requirements.txt)", out)
        self.assertIn("edge: nothing for the edge changed", out)

    def test_a_new_migration_lists_what_the_owner_must_apply_first(self) -> None:
        target = self.commit({f"{MIGRATIONS}/0002_b.py": "revision = 'b'\ndown_revision = 'a'\n"}, "migration")
        out = self.plan(target, self.base, self.base, db="a")
        self.assertIn("owner: FULL release", out)
        self.assertIn("schema: owner database at a, target needs b; apply first on Windows (stage D): b", out)

    def test_edge_parts_and_the_windows_are_reported(self) -> None:
        target = self.commit({"feishu-relay/adapter/index.mjs": "changed\n", "workflows/edge-relay/workflows/x.json": "{}\n"},
                             "edge change")
        out = self.plan(target, target, self.base, clock="2 1000")
        self.assertIn("edge adapter and dashboard: 1 changed path(s)", out)
        self.assertIn("edge edge workflows: 1 changed path(s)", out)
        self.assertIn("window: not now: Beijing 10:00", out)
        self.assertIn("owner: already at", out)

    def test_release_tooling_alone_never_reaches_the_owner(self) -> None:
        target = self.commit({"scripts/release_window.py": "# edited\n", "config/release-windows.json": "{}\n"}, "tooling")
        out = self.plan(target, self.base, target)
        self.assertIn("owner: nothing reaches the owner runtime", out)


class ReleaseEntryPointTests(unittest.TestCase):
    def run_release(self, *args: str, clock: str = "6 1000") -> subprocess.CompletedProcess[str]:
        return subprocess.run(["bash", str(ROOT / "scripts" / "release"), *args], env={**os.environ, "RELEASE_CLOCK": clock},
                              capture_output=True, text=True, check=False)

    def test_usage_and_unknown_verbs(self) -> None:
        self.assertEqual(self.run_release().returncode, 2)
        self.assertEqual(self.run_release("owner", "intraday-edge").returncode, 2)
        self.assertEqual(self.run_release("deploy").returncode, 2)

    def test_window_is_the_shared_checker(self) -> None:
        self.assertEqual(self.run_release("window", "edge-adapter", clock="2 1000").returncode, 3)
        self.assertEqual(self.run_release("window", "edge-adapter", clock="2 2300").returncode, 0)

    def test_every_verb_runs_an_existing_script_and_the_retired_one_is_absent(self) -> None:
        text = (ROOT / "scripts" / "release").read_text(encoding="utf-8")
        for script in ("scripts/release_plan.py", "scripts/release-sync-status.sh", "scripts/release_window.py",
                       "scripts/shared-peer/deploy-full-release.sh", "scripts/shared-peer/deploy-code-only.sh",
                       "feishu-relay/scripts/edge/deploy-feishu-relay-edge-release.sh",
                       "feishu-relay/scripts/edge/hotfix-feishu-relay-edge.sh",
                       "feishu-relay/scripts/edge/deploy-edge-relay-workflows.sh"):
            self.assertIn(f'"$root/{script}"', text)
            self.assertTrue((ROOT / script).exists(), script)
        self.assertNotIn('"$root/scripts/deploy-intraday-edge-release.sh"', text)


if __name__ == "__main__":
    unittest.main()
