"""规范必须躺在改代码的那个目录里，而且不能和清单漂移。

一个空上下文的 agent（codex 或 claude code）只会读它要改的那棵子树上的
``AGENTS.md``。所以"热更新不重建镜像""不许跨组件"这些规矩如果只写在仓库根，
改 ``xhs-intel/`` 的 agent 就可能一次都没看到。

这里把三件事变成会红的检查：
1. 每个组件的代码目录下都有自己的 ``AGENTS.md``；
2. 清单里声明的每条热更新路径（脚本）都被某个嵌套 ``AGENTS.md`` 点名 ——
   脚本改名、新增发布单元而没写文档，都会红；
3. 组件自己声明的测试命令，在根或嵌套 ``AGENTS.md`` 里能原样找到。

反过来也查：一个位于组件目录里的嵌套 ``AGENTS.md``，必须点到清单里的热更新
脚本并写明"不重建镜像"，否则它就是一份会把人带错的本地规范。
"""
from __future__ import annotations

import json
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
MANIFEST = json.loads((ROOT / "config" / "components.json").read_text(encoding="utf-8"))
HOT_UPDATE = MANIFEST.get("hot_update_paths") or {}

#: 允许的"不重建镜像"措辞 —— 中英文都行，但必须有一条。
NO_REBUILD_MARKERS = ("不重建镜像", "never an image rebuild", "No image is rebuilt",
                      "no image is rebuilt", "--no-build")


def nested_instruction_files() -> dict[pathlib.Path, str]:
    """仓库里除根以外的所有 AGENTS.md（排除 node_modules）。"""
    out: dict[pathlib.Path, str] = {}
    for path in ROOT.rglob("AGENTS.md"):
        rel = path.relative_to(ROOT)
        if rel == pathlib.Path("AGENTS.md"):
            continue
        if "node_modules" in rel.parts:
            continue
        out[rel] = path.read_text(encoding="utf-8")
    return out


def component_code_dirs(component: dict) -> set[str]:
    """组件 paths 里真正装着源码的顶层目录。"""
    dirs = set()
    for pattern in component["paths"]:
        head = pattern.split("/", 1)[0]
        if "*" in head:
            continue
        if (ROOT / head).is_dir():
            dirs.add(head)
    return dirs


class ComponentInstructionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.nested = nested_instruction_files()
        self.root_text = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
        self.all_text = self.root_text + "\n" + "\n".join(self.nested.values())

    def test_every_component_code_dir_has_local_instructions(self) -> None:
        documented = {rel.parts[0] for rel in self.nested}
        for component in MANIFEST["components"]:
            for code_dir in component_code_dirs(component):
                # deploy/ 是 compose 与基础设施，不是日常改代码的地方。
                if code_dir == "deploy":
                    continue
                self.assertIn(
                    code_dir, documented,
                    f"组件 {component['id']} 的代码目录 {code_dir}/ 下没有 AGENTS.md，"
                    "空上下文的 agent 在那里改代码时读不到规范")

    def test_declared_hot_update_scripts_are_named_locally(self) -> None:
        for key, entry in HOT_UPDATE.items():
            script = entry["script"]
            holders = [str(rel) for rel, text in self.nested.items() if script in text]
            self.assertTrue(
                holders,
                f"热更新路径 {key} 的脚本 {script} 没有被任何嵌套 AGENTS.md 点名；"
                "脚本改名或新增发布单元后必须同步本地规范")

    def test_nested_instructions_point_at_a_declared_hot_update_path(self) -> None:
        declared = {entry["script"] for entry in HOT_UPDATE.values()}
        owners: dict[str, set[str]] = {}
        for component in MANIFEST["components"]:
            for code_dir in component_code_dirs(component):
                owners.setdefault(code_dir, set()).add(component["id"])
        for rel, text in self.nested.items():
            sole_owner = owners.get(rel.parts[0], set())
            if len(sole_owner) != 1:
                # 根目录之外、或多组件共用的目录（workflows/）按文件名归属，
                # 没有单一的热更新脚本 —— 由下面那条检查管。
                continue
            if len(rel.parts) > 2:  # 更深层的子目录文档（例如 dashboard/）只补细节
                continue
            named = sorted(script for script in declared if script in text)
            self.assertTrue(
                named, f"{rel} 没有指向清单里任何一条热更新路径")
            self.assertTrue(
                any(marker in text for marker in NO_REBUILD_MARKERS),
                f"{rel} 没有写明日常改动不重建镜像")

    def test_shared_directories_declare_ownership_by_pattern(self) -> None:
        owners: dict[str, set[str]] = {}
        for component in MANIFEST["components"]:
            for code_dir in component_code_dirs(component):
                owners.setdefault(code_dir, set()).add(component["id"])
        shared = {d for d, ids in owners.items() if len(ids) > 1 and d != "deploy"}
        for code_dir in shared:
            doc = ROOT / code_dir / "AGENTS.md"
            self.assertTrue(doc.is_file(), f"{code_dir}/ 被多个组件共用却没有 AGENTS.md")
            text = doc.read_text(encoding="utf-8")
            for component in MANIFEST["components"]:
                for pattern in component["paths"]:
                    if not pattern.startswith(code_dir + "/"):
                        continue
                    tail = pattern.split("/", 1)[1]
                    self.assertIn(
                        tail, text,
                        f"{code_dir}/AGENTS.md 没有写明 {tail} 归 {component['id']}")

    def test_component_test_commands_are_written_down(self) -> None:
        for component in MANIFEST["components"]:
            for command in component["tests"]:
                self.assertIn(
                    command, self.all_text,
                    f"组件 {component['id']} 声明的测试命令没有写进任何 AGENTS.md: {command}")

    def test_root_points_readers_at_the_nested_files(self) -> None:
        self.assertIn("AGENTS.md", self.root_text)
        self.assertTrue(
            "config/components.json" in self.root_text,
            "根 AGENTS.md 必须指向机器可读的组件清单")


if __name__ == "__main__":
    unittest.main()
