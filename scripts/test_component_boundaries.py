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
        # ``edge-quant-console`` 是第三个单元：quant 控制台（frontend/）的源码归本
        # 组件，但静态资源跑在 47edge（适配器同源代理两个 SPA）。它以前被打包进
        # feishu-relay 的原子发布目录，于是一次 xhs 热部署会顺带重发 quant 控制台、
        # 而 quant 改完前端要等中继发布才生效。现在它自己发自己。
        self.assertEqual(releases["quant-research"],
                         ["owner-quant", "owner-schema", "edge-quant-console"])
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
