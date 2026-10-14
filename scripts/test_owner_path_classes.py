"""What the owner release classifier says about the whole repository (scripts/shared-peer/classify-owner-paths.sh)."""
from __future__ import annotations

import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"


def classes() -> dict[str, str]:
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, check=True, capture_output=True, text=True).stdout.strip()
    rows = subprocess.run(["bash", "scripts/shared-peer/classify-owner-paths.sh", EMPTY_TREE, head], cwd=ROOT, check=True,
                          capture_output=True, text=True).stdout.splitlines()
    return {path: kind for kind, path in (row.split("\t", 1) for row in rows)}


class OwnerPathClassTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.classes = classes()

    def test_every_tracked_path_has_a_rule(self) -> None:
        self.assertEqual(sorted(path for path, kind in self.classes.items() if kind == "unclassified"), [])

    def test_a_script_the_hosts_units_run_is_part_of_the_hosts_tree(self) -> None:
        units = "".join(path.read_text(encoding="utf-8") for path in (ROOT / "deploy/shared-peer/systemd").iterdir())
        run = set(re.findall(r"ExecStart=%h/trading_hareness/(scripts/\S+)", units))
        self.assertTrue(run)
        self.assertEqual({path: self.classes[path] for path in run}, {path: "full" for path in run},
                         "the catalog (scripts/generate_scripts_catalog.py) must say 47owner for a script a unit runs")

    def test_a_script_the_host_runs_without_a_unit_is_part_of_the_hosts_tree(self) -> None:
        """Called by the host's own scripts or by hand on the host (OPERATIONS.md, RELEASE_SYNC_47.md): no unit names them."""
        run = ["scripts/shared-peer/release-lock.sh", "scripts/shared-peer/activate-peer-release.sh",
               "scripts/shared-peer/verify-owner-cutover.py", "scripts/cutover/export_intraday_watchlist.py",
               "scripts/cutover/import_intraday_watchlist.py", "scripts/cutover/verify_owner_longhu_priority.py",
               "scripts/fill-longhu-sector-membership.py"]
        self.assertEqual({path: self.classes[path] for path in run}, {path: "full" for path in run})

    def test_the_tunnel_image_inputs_have_their_own_kind(self) -> None:
        self.assertEqual(sorted(path for path, kind in self.classes.items() if kind == "tunnel-image"),
                         ["deploy/shared-peer/Dockerfile.ssh-tunnel", "deploy/shared-peer/ssh-tunnel-entrypoint.sh"])

    def test_nothing_the_image_copies_is_left_to_the_hosts_tree(self) -> None:
        """A COPY source is runtime, a migration or an image input. The skipped ones are named here, one by one."""
        copied = re.findall(r"^COPY (?!--from)(\S+) ", (ROOT / "quant-service/Dockerfile.peer").read_text(encoding="utf-8"), re.M)
        self.assertIn("quant-service/app", copied)
        # A document inside a copied directory is skipped like any other document.
        kinds = {source: {kind for path, kind in self.classes.items()
                          if (path == source or path.startswith(source + "/")) and not path.endswith(".md")}
                 for source in copied}
        self.assertEqual([source for source, found in kinds.items() if not found], [], "a COPY source matches no tracked path")
        # The tests are not imported by the app. The two tools have no caller inside the image; its copies are
        # refreshed when an image is next built, and a change to them alone is not worth a build.
        skipped = {"quant-service/tests", "scripts/audit-fuyao-capabilities.py", "scripts/import-stock-brain-portfolio.py"}
        self.assertEqual({source for source, found in kinds.items() if "skip" in found}, skipped)
        self.assertEqual({source: found - {"runtime", "migration", "image"} for source, found in kinds.items() if source not in skipped},
                         {source: set() for source in copied if source not in skipped})
        self.assertEqual(kinds["quant-service/requirements.txt"], {"image"})
        self.assertEqual(self.classes[".dockerignore"], "image")
        self.assertEqual({self.classes["quant-service/alembic.ini"], self.classes["quant-service/migrations/env.py"]}, {"migration"})


class OwnerHostAddressTests(unittest.TestCase):
    """The owner relay host changes (2026-10-13, and again): its address is written once."""

    def test_only_the_shared_file_carries_the_owner_address(self) -> None:
        shared = (ROOT / "scripts/shared-peer/owner-host.sh").read_text(encoding="utf-8")
        address = re.search(r'OWNER_PEER_HOST:=[a-z]+@([0-9.]+)\}', shared).group(1)
        tracked = subprocess.run(["git", "grep", "-l", "-F", address, "--", "scripts", "quant-service/app", "feishu-relay/adapter",
                                  "feishu-relay/scripts", "xhs-intel", "deploy", "config"], cwd=ROOT, capture_output=True, text=True).stdout.split()
        # The teacher review's default predates the shared file and the isolation test names the address on purpose.
        allowed = {"scripts/shared-peer/owner-host.sh", "scripts/teacher_review_daily.py", "scripts/test_release_unit_isolation.py"}
        self.assertEqual(sorted(set(tracked) - allowed), [], "read the address from scripts/shared-peer/owner-host.sh")

    def test_every_owner_tool_reads_the_shared_file(self) -> None:
        for tool in ("scripts/release-sync-status.sh", "scripts/shared-peer/deploy-code-only.sh", "scripts/shared-peer/deploy-full-release.sh",
                     "scripts/shared-peer/schema-gate.sh", "scripts/shared-peer/owner-host-switch.sh", "scripts/sync-xiaojie-message-features.sh"):
            with self.subTest(tool=tool):
                self.assertIn("owner-host.sh", (ROOT / tool).read_text(encoding="utf-8"))
        env = {**os.environ, "OWNER_PEER_HOST": "someone@elsewhere"}
        shown = subprocess.run(["bash", "-c", ". scripts/shared-peer/owner-host.sh; echo $OWNER_PEER_HOST $OWNER_PEER_PORT"], cwd=ROOT, env=env,
                               capture_output=True, text=True, check=True).stdout.split()
        self.assertEqual(shown, ["someone@elsewhere", "22"], "the environment still overrides for one run")

    def test_the_switch_tool_never_writes_to_the_current_host_and_builds_nothing(self) -> None:
        tool = (ROOT / "scripts/shared-peer/owner-host-switch.sh").read_text(encoding="utf-8")
        self.assertNotRegex(tool, r"docker (compose )?build|docker pull")
        on_the_current_host = [line.split("|", 1)[0] for line in tool.splitlines() if line.strip().startswith("old ")]
        self.assertTrue(on_the_current_host)
        for command in on_the_current_host:   # what runs there, before any pipe to the new host
            self.assertNotRegex(command, r"(?<!--)\b(rm|mv|ln|tee|chmod|systemctl --user (enable|disable|start|stop)|docker (compose )?(up|stop|rm|load))\b")
        current = re.search(r"OWNER_PEER_HOST:=([^}]+)\}", (ROOT / "scripts/shared-peer/owner-host.sh").read_text(encoding="utf-8")).group(1)
        refused = subprocess.run(["bash", "scripts/shared-peer/owner-host-switch.sh", "copy", current, "--apply"],
                                 cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(refused.returncode, 2)
        self.assertIn("is the current owner host", refused.stderr)


class PairOfCommitsTests(unittest.TestCase):
    """What depends on both commits: the catalog rows of each, moved and deleted files."""

    def setUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory()
        self.repo = Path(self._directory.name)
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.email", "test@example.invalid")
        self.git("config", "user.name", "test")

    def tearDown(self) -> None:
        self._directory.cleanup()

    def git(self, *args: str) -> str:
        return subprocess.run(["git", *args], cwd=self.repo, check=True, capture_output=True, text=True).stdout.strip()

    def commit(self, files: dict[str, str | None], catalog: dict[str, str] | None = None) -> str:
        if catalog is not None:
            files = {**files, "scripts/CATALOG.md": "| Script | Owner | Runs on | Purpose |\n| --- | --- | --- | --- |\n" + "".join(
                f"| `{name}` | platform | {host} | a|b |\n" for name, host in catalog.items())}
        for name, text in files.items():
            path = self.repo / name
            if text is None:
                path.unlink()
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        self.git("add", "-A")
        self.git("commit", "-q", "--allow-empty", "-m", "c")
        return self.git("rev-parse", "HEAD")

    def classify(self, before: str, after: str, **env: str) -> dict[str, str]:
        rows = subprocess.run(["bash", str(ROOT / "scripts/shared-peer/classify-owner-paths.sh"), before, after], cwd=self.repo,
                              check=True, capture_output=True, text=True, env={**os.environ, **env}).stdout.splitlines()
        return {path: kind for kind, path in (row.split("\t", 1) for row in rows)}

    def test_a_label_the_classifier_does_not_name_is_refused(self) -> None:
        base = self.commit({"scripts/a.py": "1\n", "scripts/b.py": "1\n", "scripts/c.py": "1\n", "scripts/d.py": "1\n"},
                           {"a.py": "quant container", "b.py": "workstation (some on the edge)", "c.py": "47owner (inside the container)",
                            "d.py": "never run"})
        target = self.commit({"scripts/a.py": "2\n", "scripts/b.py": "2\n", "scripts/c.py": "2\n", "scripts/d.py": "2\n"})
        self.assertEqual(self.classify(base, target),
                         {"scripts/a.py": "unclassified", "scripts/b.py": "skip", "scripts/c.py": "full", "scripts/d.py": "skip"})

    def test_a_deleted_script_is_classified_by_the_row_it_had(self) -> None:
        base = self.commit({"scripts/host.sh": "1\n", "scripts/mac.sh": "1\n"}, {"host.sh": "47owner", "mac.sh": "workstation"})
        target = self.commit({"scripts/host.sh": None, "scripts/mac.sh": None}, {})
        found = self.classify(base, target)
        self.assertEqual((found["scripts/host.sh"], found["scripts/mac.sh"]), ("full", "skip"))

    def test_a_moved_file_is_classified_under_both_names(self) -> None:
        base = self.commit({"scripts/host.sh": "same\n" * 20, "quant-service/app/old.py": "same\n" * 20, "docs/x.md": "1\n",
                            "quant-service/tests/test_x.py": "1\n"}, {"host.sh": "47owner"})
        self.git("mv", "scripts/host.sh", "docs/host.sh")
        self.git("mv", "quant-service/app/old.py", "quant-service/tests/old.py")
        target = self.commit({})
        self.assertEqual(self.classify(base, target), {"scripts/host.sh": "full", "docs/host.sh": "skip",
                                                       "quant-service/app/old.py": "runtime", "quant-service/tests/old.py": "skip"})

    def test_a_row_that_moves_across_the_owner_boundary_ships_the_script(self) -> None:
        """While the row said another host, changes to the script were skipped: the host's copy may be old."""
        base = self.commit({"scripts/host.sh": "1\n", "scripts/mac.sh": "1\n", "scripts/gone.sh": "1\n", "docs/x.md": "1\n"},
                           {"host.sh": "workstation", "mac.sh": "47owner", "gone.sh": "workstation and CI"})
        relabelled = self.commit({"docs/x.md": "2\n"}, {"host.sh": "47owner", "mac.sh": "47owner", "gone.sh": "workstation"})
        note = " (not in this diff: its catalog row moved across the owner-host boundary)"
        self.assertEqual(self.classify(base, relabelled),
                         {"scripts/CATALOG.md": "skip", "docs/x.md": "skip", "scripts/host.sh" + note: "full"})
        self.assertEqual(self.classify(base, relabelled, RELEASE_RELABELLED_CURRENT="scripts/other.sh scripts/host.sh"),
                         {"scripts/CATALOG.md": "skip", "docs/x.md": "skip"})
        # The other direction, with the script in the diff: the host still runs the old copy until a release removes it.
        away = self.commit({"scripts/mac.sh": "2\n"}, {"host.sh": "47owner", "mac.sh": "workstation", "gone.sh": "workstation"})
        self.assertEqual(self.classify(relabelled, away), {"scripts/CATALOG.md": "skip", "scripts/mac.sh": "full"})
        self.assertEqual(self.classify(relabelled, away, RELEASE_RELABELLED_CURRENT="scripts/mac.sh")["scripts/mac.sh"], "skip")

    def test_paths_under_scripts_that_the_catalog_leaves_out_are_skipped_and_other_gateways_are_not(self) -> None:
        base = self.commit({"README.md": "1\n"}, {})
        target = self.commit({"scripts/pkg/__init__.py": "", "scripts/shared-peer/test_helper.sh": "1\n",
                              "scripts/test_fixtures/sample.json": "{}\n", "deploy/other/x.conf": "1\n",
                              "deploy/quant-research-gateway.nginx.conf": "1\n"})
        self.assertEqual(self.classify(base, target), {
            "scripts/pkg/__init__.py": "skip", "scripts/shared-peer/test_helper.sh": "skip", "scripts/test_fixtures/sample.json": "skip",
            "deploy/other/x.conf": "unclassified", "deploy/quant-research-gateway.nginx.conf": "skip"})


if __name__ == "__main__":
    unittest.main()
