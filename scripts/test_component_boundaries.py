#!/usr/bin/env python3
import json
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CHECKER = ROOT / "scripts" / "verify_component_boundaries.py"


class ComponentBoundaryTests(unittest.TestCase):
    def test_manifest_declares_three_runtime_components(self):
        document = json.loads((ROOT / "config" / "components.json").read_text(encoding="utf-8"))
        self.assertEqual(
            {item["id"] for item in document["components"]},
            {"quant-research", "feishu-relay", "xhs-intel"},
        )
        releases = {item["id"]: item["release_units"] for item in document["components"]}
        self.assertEqual(releases["quant-research"], ["owner-quant", "owner-schema"])
        self.assertEqual(releases["feishu-relay"], ["edge-relay", "edge-workflows"])
        for item in document["components"]:
            self.assertTrue(item["standalone"]["build_context"])
            self.assertTrue(item["standalone"]["compose"])
            self.assertTrue(item["standalone"]["external_contracts"])
            self.assertTrue(item["manifest"].endswith("/component.json"))

    def test_changed_paths_are_classified_by_runtime_owner(self):
        result = subprocess.run(
            [sys.executable, str(CHECKER),
             "quant-service/app/main.py",
             "feishu-relay/adapter/index.mjs",
             "xhs-intel/edge_api.py",
             "compose.yaml"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("impacted components: quant-research, feishu-relay, xhs-intel, integration", result.stdout)

    def test_current_source_has_no_cross_component_imports(self):
        result = subprocess.run(
            [sys.executable, str(CHECKER), "--check"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("component boundary check passed", result.stdout)


if __name__ == "__main__":
    unittest.main()
