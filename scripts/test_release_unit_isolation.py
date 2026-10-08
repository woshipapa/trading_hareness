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
CONSOLE_SCRIPT = ROOT / "scripts" / "edge" / "deploy-quant-console-edge.sh"

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
            self.assertFalse(marker in body,
                             f"edge 热部署脚本提到了 owner 的 {marker}；它只应该操作 edge")

    def test_the_owner_release_never_names_the_edge_host(self) -> None:
        body = _body(OWNER_SCRIPT)
        for marker in EDGE_MARKERS:
            self.assertFalse(marker in body,
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


def _dirty_pathspec(path: pathlib.Path) -> list[str]:
    """脚本判断工作区是否干净时用的 pathspec。"""
    text = path.read_text(encoding="utf-8")
    match = re.search(r"diff --quiet --ignore-submodules -- \\?\s*([^;\n|]+)", text)
    assert match, f"{path.name} 里找不到脏检查"
    return match.group(1).split()


class DirtyCheckTests(unittest.TestCase):
    """两个发布单元各自的脏检查都必须盯被跟踪的源码，不能盯被忽略的构建产物。

    ``frontend/dist`` 和 ``feishu-relay/dashboard/dist`` 都在 .gitignore 里，
    ``git diff`` 对它们永远返回 0。拿它们当 pathspec 等于没有检查 —— 带着未提交
    的改动热部署，改动会被构建发出去，而 release_id 上不带 ``-dirty``。
    """

    def test_no_pathspec_is_a_gitignored_build_directory(self) -> None:
        for script in (EDGE_SCRIPT, CONSOLE_SCRIPT):
            for entry in _dirty_pathspec(script):
                ignored = subprocess.run(["git", "check-ignore", "-q", entry], cwd=ROOT).returncode == 0
                self.assertFalse(ignored, f"{script.name} 的 pathspec {entry} 被 .gitignore 忽略，"
                                          "git diff 永远看不见它")

    def test_each_unit_watches_only_its_own_sources(self) -> None:
        relay = _dirty_pathspec(EDGE_SCRIPT)
        console = _dirty_pathspec(CONSOLE_SCRIPT)
        self.assertIn("feishu-relay", relay)
        self.assertNotIn("frontend", relay,
                         "quant 控制台已拆成独立单元，中继的脏检查不该再管 frontend/")
        self.assertIn("frontend", console)
        self.assertNotIn("feishu-relay", console,
                         "控制台发布不该因为中继脏了就被标记")

    def test_a_dirty_quant_frontend_is_detected_by_its_own_unit(self) -> None:
        probe = ROOT / "frontend" / "src" / "App.vue"
        if not probe.is_file():
            self.skipTest("frontend/src/App.vue 不在")
        def clean(script: pathlib.Path) -> bool:
            return subprocess.run(
                ["git", "diff", "--quiet", "--ignore-submodules", "--", *_dirty_pathspec(script)],
                cwd=ROOT).returncode == 0

        # 中继那边**本来**是不是干净的，取决于工作区里有没有别人的改动，所以不能
        # 直接断言它干净 —— 要看探针有没有**改变**它的结论。（这个坑我踩过一次：
        # 当时另一个 agent 正在改 feishu-relay，把结果带脏了。）
        original = probe.read_bytes()
        relay_before = clean(EDGE_SCRIPT)
        try:
            probe.write_bytes(original + b"\n<!-- isolation probe -->\n")
            self.assertFalse(clean(CONSOLE_SCRIPT), "quant 前端脏了，控制台发布却认为干净")
            self.assertEqual(clean(EDGE_SCRIPT), relay_before,
                             "只改 quant 前端，却改变了中继发布的出处判断")
        finally:
            probe.write_bytes(original)


class FrontendUnitSeparationTests(unittest.TestCase):
    """中继热部署不再构建或发布 quant 控制台，反之亦然。"""

    def test_the_relay_hotfix_no_longer_builds_or_ships_the_console(self) -> None:
        text = EDGE_SCRIPT.read_text(encoding="utf-8")
        body = _body(EDGE_SCRIPT)
        # 用布尔断言，别把整份 600 行脚本喷到失败输出里
        self.assertFalse("quant-frontend-dist" in body,
                         "中继发布目录里不该再有 quant 控制台；它由 "
                         "scripts/edge/deploy-quant-console-edge.sh 独立发布")
        self.assertFalse("repo_root/frontend" in body,
                         "中继热部署不该再构建 quant 控制台")
        # 飞书自己的面板仍然由中继发布
        self.assertIn("frontend-dist", text)

    def test_the_console_publisher_does_not_touch_the_relay_release(self) -> None:
        body = _body(CONSOLE_SCRIPT)
        self.assertFalse("hotfix/current" in body,
                         "发布控制台不该碰中继的 current 发布目录")
        for marker in ("adapter/index.mjs", "bridge/bridge.py", "source-registry.json"):
            self.assertFalse(marker in body, f"控制台发布不该涉及中继的 {marker}")

    def test_the_console_publisher_only_targets_the_edge_host(self) -> None:
        body = _body(CONSOLE_SCRIPT)
        for marker in OWNER_MARKERS:
            self.assertFalse(marker in body, f"控制台发布脚本提到了 owner 的 {marker}")

    def test_the_adapter_reads_the_console_from_its_own_tree(self) -> None:
        import yaml
        compose = yaml.safe_load(
            (ROOT / "feishu-relay" / "deploy" / "edge" / "docker-compose.yml").read_text(encoding="utf-8"))
        command = " ".join(compose["services"]["feishu-adapter"]["command"])
        self.assertIn("QUANT_FRONTEND_DIST=/app/hotfix/quant-console/current", command)
        self.assertNotIn("QUANT_FRONTEND_DIST=/app/hotfix/current/quant-frontend-dist", command)


class CrossUnitArtifactTests(unittest.TestCase):
    """跨发布单元的产物必须写明谁拥有、谁发布，并与脚本一致。"""

    def setUp(self) -> None:
        import json
        self.manifest = json.loads((ROOT / "config" / "components.json").read_text(encoding="utf-8"))
        self.entries = self.manifest.get("cross_unit_artifacts") or []

    def test_the_console_is_declared_as_its_own_release_unit(self) -> None:
        entry = next((item for item in self.entries if item["artifact"] == "frontend/dist"), None)
        self.assertIsNotNone(entry)
        self.assertEqual(entry["owned_by"], "quant-research")
        self.assertEqual(entry["published_by"], "edge-quant-console")
        quant = next(c for c in self.manifest["components"] if c["id"] == "quant-research")
        self.assertIn("edge-quant-console", quant["release_units"])

    def test_the_declared_publisher_really_ships_it(self) -> None:
        for entry in self.entries:
            publisher = ROOT / entry["publisher"]
            self.assertTrue(publisher.is_file(), f"声明的发布者不存在：{entry['publisher']}")
            self.assertIn(entry["artifact"], publisher.read_text(encoding="utf-8"),
                          f"{entry['publisher']} 并没有发布 {entry['artifact']}")


if __name__ == "__main__":
    unittest.main()
