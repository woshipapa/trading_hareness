"""workflows/CATALOG.md 必须与生成器输出一致，否则 workflow 变更会丢失目录行。"""
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import generate_workflows_catalog as catalog  # noqa: E402


class WorkflowsCatalogTests(unittest.TestCase):
    def test_committed_catalog_matches_the_generator(self):
        if not (ROOT / "workflows" / "managed" / "workflows").is_dir():
            # The workstation mirror is gitignored; without it the committed
            # catalog (generated where the mirror exists) cannot be reproduced.
            self.skipTest("workflows/managed not exported on this machine")
        self.assertTrue(catalog.OUTPUT.exists(), "workflows/CATALOG.md is missing")
        self.assertEqual(catalog.OUTPUT.read_text(encoding="utf-8"), catalog.render(),
                         "workflows/CATALOG.md is stale; run python3 scripts/generate_workflows_catalog.py")

    def test_every_workflow_file_is_classified(self):
        text = catalog.render()
        self.assertNotIn("未归属", text,
                         "a workflow matches no ownership rule; extend OWNERSHIP or components.json")

    def test_catalog_covers_both_instances(self):
        text = catalog.OUTPUT.read_text(encoding="utf-8")
        self.assertIn("本机 n8n", text)
        self.assertIn("边缘 n8n", text)
        self.assertIn("xhs-intel-edge-daily-v1", text)


if __name__ == "__main__":
    unittest.main()
