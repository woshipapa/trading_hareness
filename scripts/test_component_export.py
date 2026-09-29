#!/usr/bin/env python3
import json
from pathlib import Path
import tempfile
import tarfile
import unittest

from export_component import export_component


ROOT = Path(__file__).resolve().parents[1]


class ComponentExportTests(unittest.TestCase):
    def test_feishu_export_is_component_scoped_and_secret_free(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = export_component("feishu-relay", Path(temporary) / "feishu.tar.gz")
            with tarfile.open(output, "r:gz") as archive:
                names = archive.getnames()
                manifest = json.load(archive.extractfile("COMPONENT_MANIFEST.json"))
            self.assertIn("feishu-relay/adapter/index.mjs", names)
            self.assertIn("feishu-relay/adapter/Dockerfile.standalone", names)
            self.assertIn("feishu-relay/compose.standalone.yaml", names)
            self.assertIn("feishu-relay/dashboard/package.json", names)
            self.assertIn("feishu-relay/adapter/package-lock.json", names)
            self.assertIn("feishu-relay/bridge/requirements.lock", names)
            self.assertIn("COMPONENT_MANIFEST.json", names)
            self.assertNotIn("quant-service/app/main.py", names)
            self.assertFalse(any(Path(name).name in {".env", ".env.local", ".env.production"} for name in names))
            self.assertEqual(manifest["component"]["id"], "feishu-relay")
            self.assertEqual(manifest["component"]["release_units"], ["edge-relay", "edge-workflows"])
            self.assertEqual(manifest["source_root"], "repository-root")
            self.assertEqual(manifest["local_manifest"], "feishu-relay/component.json")
            self.assertNotIn(str(ROOT), json.dumps(manifest))

    def test_quant_export_excludes_build_and_cache_directories(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = export_component("quant-research", Path(temporary) / "quant.tar.gz")
            with tarfile.open(output, "r:gz") as archive:
                names = archive.getnames()
            self.assertIn("quant-service/app/main.py", names)
            self.assertIn("quant-service/Dockerfile.standalone", names)
            self.assertIn("quant-service/compose.standalone.yaml", names)
            self.assertIn("quant-service/requirements.lock", names)
            self.assertIn("frontend/src/App.vue", names)
            self.assertFalse(any("__pycache__" in name or "node_modules" in name or name.startswith("frontend/dist/") for name in names))

    def test_xhs_export_keeps_the_external_source_as_a_declared_runtime_contract(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = export_component("xhs-intel", Path(temporary) / "xhs.tar.gz")
            with tarfile.open(output, "r:gz") as archive:
                names = archive.getnames()
            self.assertIn("xhs-intel/Dockerfile.runtime", names)
            self.assertIn("xhs-intel/compose.standalone.yaml", names)
            self.assertIn("xhs-intel/requirements.lock", names)
            self.assertNotIn("Spider_XHS/", "\n".join(names))


if __name__ == "__main__":
    unittest.main()
