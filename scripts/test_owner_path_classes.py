"""What the owner release classifier says about the whole repository (scripts/shared-peer/classify-owner-paths.sh)."""
from __future__ import annotations

import re
import subprocess
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

    def test_nothing_the_image_copies_is_left_to_the_hosts_tree(self) -> None:
        """A COPY source is runtime, a migration, an image input or deliberately skipped: never an unknown."""
        copied = re.findall(r"^COPY (?!--from)(\S+) ", (ROOT / "quant-service/Dockerfile.peer").read_text(encoding="utf-8"), re.M)
        self.assertIn("quant-service/app", copied)
        kinds = {source: {kind for path, kind in self.classes.items() if path == source or path.startswith(source + "/")}
                 for source in copied}
        self.assertEqual({source: found - {"runtime", "migration", "image", "skip"} for source, found in kinds.items()},
                         {source: set() for source in copied})
        self.assertEqual(kinds["quant-service/requirements.txt"], {"image"})


if __name__ == "__main__":
    unittest.main()
