"""两条热部署路径必须互不影响 —— 用检查钉住，不靠人记得。

要保的两件事：

* 改 feishu-relay（含 xhs、分析师相关）热部署 47edge，**不能动到 47owner**；
* 47owner 每天复盘的策略更新与迭代，**不能动到 47edge**。

主机层面本来就是分开的，但有两处曾经不是：

1. owner 的 code-only 发布一旦遇到不认识的路径必须 fail closed，而且必须把
   ``feishu-relay/*``、``frontend/*`` 明确归到"不属于 owner 运行时"，否则哪天
   有人把 edge 代码一起发进 owner 运行时都不会有人发现。
2. edge 热部署会**构建并发布 quant 的前端**（``frontend/`` 属于 quant-research
   组件）。它判断工作区是否干净时用的 pathspec 是 ``frontend/dist`` 和
   ``feishu-relay/dashboard/dist`` —— 两个都在 .gitignore 里，``git diff`` 永远
   返回 0。于是带着未提交的 quant 前端改动跑热部署，改动会被构建发到 edge，而
   release_id 上不带 ``-dirty``，出处在说谎。pathspec 必须盯源码目录。
"""
from __future__ import annotations

import pathlib
import re
import subprocess
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
EDGE_SCRIPT = ROOT / "feishu-relay" / "scripts" / "edge" / "hotfix-feishu-relay-edge.sh"
OWNER_SCRIPT = ROOT / "scripts" / "shared-peer" / "deploy-code-only.sh"

OWNER_MARKERS = ("47.110.79.189", "stockpeer", "OWNER_PEER_HOST", "OWNER_PEER_SSH_KEY")
EDGE_MARKERS = ("47.114.113.152", "EDGE_HOST")


def _body(path: pathlib.Path) -> str:
    """脚本正文，去掉注释行 —— 注释里提到对面主机不算越界。"""
    lines = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue
        lines.append(line.split(" #", 1)[0])
    return "\n".join(lines)


class HostIsolationTests(unittest.TestCase):
    def test_the_edge_hotfix_never_names_the_owner_host(self) -> None:
        body = _body(EDGE_SCRIPT)
        for marker in OWNER_MARKERS:
            self.assertNotIn(marker, body,
                             f"edge 热部署脚本提到了 owner 的 {marker}；它只应该操作 edge")

    def test_the_owner_release_never_names_the_edge_host(self) -> None:
        body = _body(OWNER_SCRIPT)
        for marker in EDGE_MARKERS:
            self.assertNotIn(marker, body,
                             f"owner 发布脚本提到了 edge 的 {marker}；它只应该操作 owner")


class OwnerAllowlistTests(unittest.TestCase):
    """owner 的 code-only 发布不能夹带 edge 代码，遇到不认识的路径要 fail closed。"""

    def setUp(self) -> None:
        self.text = OWNER_SCRIPT.read_text(encoding="utf-8")

    def test_edge_paths_are_classified_as_not_owner_runtime(self) -> None:
        skipped = re.search(r"\n\s*(quant-service/tests/\*[^)]*)\)\s*\n\s*skipped_files", self.text)
        self.assertIsNotNone(skipped, "找不到 skipped_files 那条 case 分支")
        arm = skipped.group(1)
        for pattern in ("feishu-relay/*", "frontend/*"):
            self.assertIn(pattern, arm, f"{pattern} 必须被明确归为「不属于 owner 运行时」")

    def test_only_owner_runtime_paths_are_shipped(self) -> None:
        shipped = re.search(r"\n\s*(quant-service/app/\*[^)]*)\)\s*\n\s*runtime_files", self.text)
        self.assertIsNotNone(shipped)
        arm = shipped.group(1)
        for forbidden in ("feishu-relay", "frontend", "xhs-intel"):
            self.assertNotIn(forbidden, arm, f"{forbidden} 不能进 owner 运行时文件集")

    def test_an_unknown_path_fails_closed(self) -> None:
        self.assertRegex(self.text, r"\*\)\s*echo \"full release required for: \$path\" >&2; exit 1")

    def test_a_migration_change_forces_a_full_release(self) -> None:
        self.assertIn("quant-service/migrations/versions/*.py", self.text)
        self.assertIn("full release required for", self.text)


class EdgeProvenanceTests(unittest.TestCase):
    """edge 热部署会发布 quant 前端，所以它的"工作区是否干净"必须盯源码。"""

    def setUp(self) -> None:
        self.text = EDGE_SCRIPT.read_text(encoding="utf-8")
        match = re.search(r"diff --quiet --ignore-submodules -- \\\n\s*([^;\n]+); then", self.text)
        self.assertIsNotNone(match, "找不到判断工作区是否干净的那段")
        self.pathspec = match.group(1).split()

    def test_the_dirty_check_watches_sources_not_ignored_build_output(self) -> None:
        for entry in self.pathspec:
            ignored = subprocess.run(["git", "check-ignore", "-q", entry], cwd=ROOT).returncode == 0
            self.assertFalse(ignored,
                             f"{entry} 在 .gitignore 里，git diff 永远看不见它，"
                             "拿它当 pathspec 等于没有检查")

    def test_both_published_frontends_are_covered_by_the_dirty_check(self) -> None:
        covered = set(self.pathspec)
        self.assertIn("frontend", covered, "quant 前端的源码目录必须在脏检查里")
        self.assertTrue({"feishu-relay", "feishu-relay/dashboard"} & covered,
                        "飞书面板的源码目录必须在脏检查里")

    def test_a_dirty_quant_frontend_is_actually_detected(self) -> None:
        """端到端验一次：真的改一个被跟踪的 quant 前端源码文件，必须被认出来。"""
        probe = ROOT / "frontend" / "src" / "App.vue"
        if not probe.is_file():
            self.skipTest("frontend/src/App.vue 不在")
        original = probe.read_bytes()
        try:
            probe.write_bytes(original + b"\n<!-- isolation probe -->\n")
            clean = subprocess.run(
                ["git", "diff", "--quiet", "--ignore-submodules", "--", *self.pathspec],
                cwd=ROOT).returncode == 0
            self.assertFalse(clean, "quant 前端源码脏了，脚本却认为工作区干净")
        finally:
            probe.write_bytes(original)


class CrossUnitArtifactTests(unittest.TestCase):
    """跨发布单元的产物必须在清单里写明谁拥有、谁发布。

    quant 的仪表盘源码属 quant-research，构建与发布却在 edge 热部署里。这是
    "owner 的迭代会不会影响 edge"唯一还成立的一条，所以要写出来，而不是口头相传。
    """

    def setUp(self) -> None:
        import json
        self.manifest = json.loads((ROOT / "config" / "components.json").read_text(encoding="utf-8"))
        self.entries = self.manifest.get("cross_unit_artifacts") or []

    def test_the_quant_dashboard_is_declared_as_edge_published(self) -> None:
        entry = next((item for item in self.entries if item["artifact"] == "frontend/dist"), None)
        self.assertIsNotNone(entry, "frontend/dist 由 edge 发布，必须在 cross_unit_artifacts 里声明")
        self.assertEqual(entry["owned_by"], "quant-research")
        self.assertEqual(entry["published_by"], "edge-relay")

    def test_the_declared_publisher_really_ships_it(self) -> None:
        """声明要和脚本一致：谁改了发布方，这条就会红。"""
        for entry in self.entries:
            publisher = ROOT / entry["publisher"]
            self.assertTrue(publisher.is_file(), f"声明的发布者不存在：{entry['publisher']}")
            self.assertIn(entry["artifact"], publisher.read_text(encoding="utf-8"),
                          f"{entry['publisher']} 并没有发布 {entry['artifact']}")


if __name__ == "__main__":
    unittest.main()
