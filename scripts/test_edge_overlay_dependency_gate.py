"""overlay 的依赖闸门必须只拦**依赖**变化。

overlay 复用镜像里的 ``node_modules``，所以 ``adapter/package.json`` 的依赖一变，
source overlay 就不够了，必须发不可变镜像 —— 这道闸门是对的。

但它原来比的是整个文件的 sha256。2026-10-08 实测被它拦住，查下来差异只有：

    + "scripts": { "test": "node --test *.test.mjs" }

那是组件拆分给各组件加标准测试入口留下的，依赖一个都没变，对运行期毫无影响。
一道会因为无关字段误拦的闸门，真正的代价是逼人去绕它或干脆关掉它。

现在只比依赖相关字段（和 owner 侧 ``migration_code_unchanged()`` 忽略注释只比
代码是同一个思路）。这里把那段比较逻辑从脚本里抽出来真跑一遍。
"""
from __future__ import annotations

import json
import pathlib
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "feishu-relay" / "scripts" / "edge" / "hotfix-feishu-relay-edge.sh"
SNIPPET = re.compile(
    r"dependency_fields_match\(\) \{\n  docker exec -i \"\$container_name\" node -e '\n(.*?)\n  ' < \"\$1\"",
    re.S)

IMAGE = {"name": "feishu-relay-adapter", "type": "module",
         "dependencies": {"@larksuiteoapi/node-sdk": "^1.0.0", "ws": "^8.0.0"}}


class DependencyGateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if shutil.which("node") is None:
            raise unittest.SkipTest("node 不在 PATH 上")
        match = SNIPPET.search(SCRIPT.read_text(encoding="utf-8"))
        if match is None:
            raise AssertionError("没在热部署脚本里找到 dependency_fields_match 的比较逻辑")
        body = (match.group(1)
                .replace('fs.readFileSync("/app/package.json", "utf8")',
                         'fs.readFileSync(process.argv[2], "utf8")')
                .replace('fs.readFileSync(0, "utf8")', 'fs.readFileSync(process.argv[3], "utf8")'))
        cls.tmp = tempfile.TemporaryDirectory()
        # 必须是 .cjs：脚本里用的是 ``node -e``（CommonJS 作用域），写成 .mjs 会
        # 因为没有 require 而整段报错 —— 那样四个用例会全部"通过"，等于没测。
        cls.checker = pathlib.Path(cls.tmp.name) / "depcheck.cjs"
        cls.checker.write_text(body, encoding="utf-8")

    @classmethod
    def tearDownClass(cls) -> None:
        cls.tmp.cleanup()

    def _matches(self, image: dict, candidate: dict) -> bool:
        root = pathlib.Path(self.tmp.name)
        (root / "image.json").write_text(json.dumps(image), encoding="utf-8")
        (root / "candidate.json").write_text(json.dumps(candidate), encoding="utf-8")
        return subprocess.run(
            ["node", str(self.checker), str(root / "image.json"), str(root / "candidate.json")],
            capture_output=True).returncode == 0

    def test_the_checker_itself_runs(self) -> None:
        """先证明被测逻辑真的跑起来了，否则下面每一条都会假通过。"""
        self.assertTrue(self._matches(IMAGE, dict(IMAGE)))

    def test_an_unrelated_scripts_block_is_allowed(self) -> None:
        candidate = dict(IMAGE, scripts={"test": "node --test *.test.mjs"})
        self.assertTrue(self._matches(IMAGE, candidate),
                        "只多了 scripts 条目却被拦 —— 那正是 2026-10-08 的假阳性")

    def test_version_and_description_churn_is_allowed(self) -> None:
        candidate = dict(IMAGE, version="9.9.9", description="改了说明")
        self.assertTrue(self._matches(IMAGE, candidate))

    def test_key_order_does_not_matter(self) -> None:
        candidate = dict(IMAGE, dependencies={"ws": "^8.0.0", "@larksuiteoapi/node-sdk": "^1.0.0"})
        self.assertTrue(self._matches(IMAGE, candidate))

    def test_a_new_dependency_is_refused(self) -> None:
        candidate = dict(IMAGE, dependencies={**IMAGE["dependencies"], "left-pad": "^1.0.0"})
        self.assertFalse(self._matches(IMAGE, candidate))

    def test_a_version_bump_is_refused(self) -> None:
        candidate = dict(IMAGE, dependencies={**IMAGE["dependencies"], "ws": "^9.0.0"})
        self.assertFalse(self._matches(IMAGE, candidate))

    def test_a_removed_dependency_is_refused(self) -> None:
        candidate = dict(IMAGE, dependencies={"ws": "^8.0.0"})
        self.assertFalse(self._matches(IMAGE, candidate))

    def test_dev_and_engine_changes_are_refused(self) -> None:
        self.assertFalse(self._matches(IMAGE, dict(IMAGE, devDependencies={"c8": "^9.0.0"})))
        self.assertFalse(self._matches(IMAGE, dict(IMAGE, engines={"node": ">=24"})))


if __name__ == "__main__":
    unittest.main()
