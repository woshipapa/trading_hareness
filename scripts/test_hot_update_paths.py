"""每个发布单元都必须有一条"不重建镜像"的热更新路径，而且必须 fail closed。

47 的发布模型是：日常源码改动走带版本的 source overlay，复用现有镜像；只有运行
时依赖或基础设施契约变了才发不可变镜像。四个单元里有三个本来就是这样，但
**xhs-intel 不是** —— ``deploy-xhs-intel-edge.sh`` 只要
``XHS_INTEL_TREE_SHA256`` 变了就 ``docker compose build xhs-collector``，也就是
改一行 Python 就重建一次镜像（那个镜像要装 nodejs/npm、pip 依赖，还要对外部
Spider_XHS 跑 npm ci）。

这里把"每个单元都有热更新路径、且都 fail closed"变成会红的检查。
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
MANIFEST = json.loads((ROOT / "config" / "components.json").read_text(encoding="utf-8"))
PATHS = MANIFEST.get("hot_update_paths") or {}

#: 单元 → 它必须重建/重启的容器服务，以及必须出现的"不重建镜像"证据。
NO_BUILD_MARKERS = ("--no-build", "--pull never")


class HotUpdateCoverageTests(unittest.TestCase):
    def test_every_release_unit_has_a_declared_hot_update_path(self) -> None:
        declared_units = {entry["unit"] for entry in PATHS.values()}
        expected = set()
        for component in MANIFEST["components"]:
            expected.update(component["release_units"])
        # owner-schema 是数据库迁移单元，本质上不能"热更新"，它要先应用再切代码。
        expected.discard("owner-schema")
        # edge-workflows 是 n8n 工作流导入，不是容器源码。
        expected.discard("edge-workflows")
        self.assertEqual(declared_units, expected,
                         "有发布单元没有声明热更新路径，或声明了不存在的单元")

    def test_each_declared_script_exists_and_is_executable(self) -> None:
        for name, entry in PATHS.items():
            script = ROOT / entry["script"]
            self.assertTrue(script.is_file(), f"{name} 声明的热更新脚本不存在：{entry['script']}")
            self.assertTrue(os.access(script, os.X_OK), f"{entry['script']} 不可执行")

    def test_each_entry_declares_it_reuses_the_existing_image(self) -> None:
        for name, entry in PATHS.items():
            self.assertTrue(entry.get("reuses_image"), f"{name} 没有声明复用现有镜像")
            self.assertTrue(entry.get("full_release_required_when"),
                            f"{name} 没有写明什么情况下必须改走镜像发布")


class NoImageBuildTests(unittest.TestCase):
    """热更新脚本不得构建或拉取运行时镜像。"""

    def _body(self, relative: str) -> str:
        lines = []
        for line in (ROOT / relative).read_text(encoding="utf-8").splitlines():
            if line.lstrip().startswith("#"):
                continue
            lines.append(line.split(" #", 1)[0])
        return "\n".join(lines)

    def test_no_hot_update_script_builds_a_runtime_image(self) -> None:
        for name, entry in PATHS.items():
            body = self._body(entry["script"])
            self.assertFalse(re.search(r"docker\s+(compose\s+)?build\b", body),
                             f"{name} 的热更新脚本里出现了 docker build —— 热更新不该重建镜像")
            self.assertFalse(re.search(r"docker\s+(compose\s+)?pull\b", body),
                             f"{name} 的热更新脚本里出现了 docker pull")

    def test_container_restarts_are_pinned_to_the_existing_image(self) -> None:
        """只要脚本会重建容器，就必须带 --no-build 和 --pull never。"""
        for name, entry in PATHS.items():
            body = self._body(entry["script"])
            if "docker compose" not in body or "up -d" not in body:
                continue  # 纯静态资源的单元不需要重启容器
            for marker in NO_BUILD_MARKERS:
                self.assertIn(marker, body,
                              f"{name} 重建容器时缺少 {marker}，可能会触发构建或拉取")


class FailClosedTests(unittest.TestCase):
    """依赖面一变必须拒绝，让人去走镜像发布。

    断言一律用布尔形式 —— ``assertIn`` 在失败时会把整份几百行的脚本喷进输出，
    谁撞上这条都得先翻一屏才知道发生了什么。
    """

    def _requires(self, unit: str, *needles: str) -> None:
        body = (ROOT / PATHS[unit]["script"]).read_text(encoding="utf-8")
        for needle in needles:
            self.assertTrue(needle in body,
                            f"{PATHS[unit]['script']} 里缺少 fail-closed 标记：{needle!r}")

    def test_the_relay_gates_on_adapter_dependencies(self) -> None:
        self._requires("feishu-relay", "dependency_fields_match",
                       "publish an immutable image release")

    def test_the_xhs_overlay_gates_on_requirements_and_dockerfile(self) -> None:
        self._requires("xhs-intel", "requirements.txt differs from the running image",
                       "xhs-intel/Dockerfile changed", "exit 42")

    def test_the_owner_path_gates_on_migrations_and_unknown_paths(self) -> None:
        self._requires("quant-research", "quant-service/migrations/versions/*.py")
        body = (ROOT / PATHS["quant-research"]["script"]).read_text(encoding="utf-8")
        self.assertTrue(
            re.search(r"\*\)\s*echo \"full release required for: \$path\" >&2; exit 1", body),
            "owner 发布脚本缺少「未知路径一律拒绝」的兜底分支")


class ComponentOwnershipTests(unittest.TestCase):
    """热更新脚本应该放在它所属组件的目录里。"""

    def test_each_script_lives_with_the_thing_it_releases(self) -> None:
        expected_prefix = {
            "feishu-relay": "feishu-relay/",
            "xhs-intel": "xhs-intel/",
        }
        for name, prefix in expected_prefix.items():
            self.assertTrue(PATHS[name]["script"].startswith(prefix),
                            f"{name} 的热更新脚本不在自己的组件目录下："
                            f"{PATHS[name]['script']}")


if __name__ == "__main__":
    unittest.main()
