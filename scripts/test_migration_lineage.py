"""The migration lineage the release gate reads, and the real repository's single head."""
from __future__ import annotations

import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "quant-service" / "scripts" / "migration_lineage.py"
VERSIONS = ROOT / "quant-service" / "migrations" / "versions"

spec = importlib.util.spec_from_file_location("migration_lineage", TOOL)
lineage_module = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(lineage_module)


class LineageTests(unittest.TestCase):
    def setUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory()
        self.versions = Path(self._directory.name)

    def tearDown(self) -> None:
        self._directory.cleanup()

    def write(self, name: str, revision: str, down: object, annotated: bool = False) -> None:
        if annotated:
            text = f"revision: str = {revision!r}\ndown_revision: str | None = {down!r}\n"
        else:
            text = f'"""doc"""\nimport alembic\nrevision = {revision!r}\ndown_revision = {down!r}\n'
        (self.versions / name).write_text(text, encoding="utf-8")

    def merge_lineage(self) -> dict[str, tuple[str, ...]]:
        self.write("1.py", "a", None)
        self.write("2.py", "b", "a", annotated=True)
        self.write("3.py", "c", "a")
        self.write("4.py", "m", ("b", "c"))
        self.write("5.py", "d", "m")
        return lineage_module.read_lineage(self.versions)

    def test_heads_status_and_pending_across_a_merge(self) -> None:
        lineage = self.merge_lineage()
        self.assertEqual(lineage_module.heads(lineage), ["d"])
        self.assertEqual(lineage_module.schema_status(lineage, "d"), "at_head")
        self.assertEqual(lineage_module.schema_status(lineage, "b"), "behind")
        self.assertEqual(lineage_module.schema_status(lineage, "zz"), "unknown")
        self.assertEqual(lineage_module.pending(lineage, "b"), ["c", "m", "d"])
        self.assertEqual(lineage_module.pending(lineage, "a"), ["b", "c", "m", "d"])

    def test_two_heads_or_a_duplicate_revision_is_an_error(self) -> None:
        self.write("1.py", "a", None)
        self.write("2.py", "b", "a")
        self.write("3.py", "c", "a")
        with self.assertRaises(ValueError):
            lineage_module.schema_status(lineage_module.read_lineage(self.versions), "a")
        self.write("4.py", "c", "b")
        with self.assertRaises(ValueError):
            lineage_module.read_lineage(self.versions)

    def test_the_command_line_exit_codes(self) -> None:
        self.merge_lineage()

        def run(*args: str) -> subprocess.CompletedProcess[str]:
            return subprocess.run([sys.executable, str(TOOL), str(self.versions), *args],
                                  capture_output=True, text=True, check=False)

        self.assertEqual(run("--head").stdout.strip(), "d")
        self.assertEqual([run("--check", rev).returncode for rev in ("d", "b", "zz")], [0, 3, 4])
        self.write("6.py", "e", "m")
        self.assertEqual(run("--head").returncode, 2)


class RepositoryLineageTests(unittest.TestCase):
    def test_the_repository_has_exactly_one_migration_head(self) -> None:
        heads = lineage_module.heads(lineage_module.read_lineage(VERSIONS))
        self.assertEqual(len(heads), 1, f"parallel migration heads {heads}: add a merge revision")


if __name__ == "__main__":
    unittest.main()
