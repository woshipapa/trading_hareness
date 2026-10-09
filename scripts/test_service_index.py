"""docs/services.html 必须与生成器一致，服务端口变更不得丢索引。"""
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import generate_service_index as index  # noqa: E402


class ServiceIndexTests(unittest.TestCase):
    def test_committed_page_matches_the_generator(self):
        self.assertTrue(index.OUTPUT.exists(), "docs/services.html is missing")
        self.assertEqual(index.OUTPUT.read_text(encoding="utf-8"), index.render(),
                         "docs/services.html is stale; run python3 scripts/generate_service_index.py")

    def test_registry_targets_are_loopback_only(self):
        for _group, name, base, _kind, _desc, _links in index.REGISTRY:
            self.assertTrue(base.startswith("http://127.0.0.1:"),
                            f"{name} 的入口必须是本机 loopback（隧道/本地服务），不得直连远端主机")

    def test_known_core_entries_present(self):
        text = index.render()
        for marker in (":18300", ":18790", ":5678", ":8787", ":15682", "/xhs/", "/monitor"):
            self.assertIn(marker, text)


if __name__ == "__main__":
    unittest.main()
