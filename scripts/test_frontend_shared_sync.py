"""前端共享层的副本只能滞后，不能分叉。

每个应用 vendored 的 ``src/shared/*`` 必须逐字节等于 canonical
（``frontend-shared/src/``）当前内容或其某个已提交的历史版本：允许应用
按自己的节奏吸收共享层更新，禁止直接修改副本造成双向分叉。
"""
import pathlib
import subprocess
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import sync_frontend_shared as sync  # noqa: E402


def committed_versions(relative: str) -> set[str]:
    revs = subprocess.run(["git", "log", "--format=%H", "--", relative],
                          cwd=ROOT, capture_output=True, text=True, check=True).stdout.split()
    versions = set()
    for rev in revs:
        shown = subprocess.run(["git", "show", f"{rev}:{relative}"],
                               cwd=ROOT, capture_output=True, text=True)
        if shown.returncode == 0:
            versions.add(shown.stdout)
    return versions


class FrontendSharedSyncTests(unittest.TestCase):
    def test_every_vendored_copy_matches_a_committed_canonical_version(self):
        for name, targets in sync.FILES.items():
            canonical_relative = f"frontend-shared/src/{name}"
            canonical = ROOT / canonical_relative
            self.assertTrue(canonical.is_file(), f"canonical missing: {canonical_relative}")
            allowed = committed_versions(canonical_relative)
            allowed.add(canonical.read_text(encoding="utf-8"))
            for app, relative in targets.items():
                target = ROOT / relative
                self.assertTrue(
                    target.is_file(),
                    f"{app} 的副本缺失：{relative}（运行 python3 scripts/sync_frontend_shared.py --apply {app}）")
                self.assertIn(
                    target.read_text(encoding="utf-8"), allowed,
                    f"{relative} 与 canonical 的任何已提交版本都不一致 —— 副本被直接修改了；"
                    f"请改 {canonical_relative} 并运行 sync_frontend_shared.py --apply {app}")

    def test_canonical_modules_do_not_import_app_code_or_new_dependencies(self):
        for name in sync.FILES:
            text = (ROOT / "frontend-shared" / "src" / name).read_text(encoding="utf-8")
            for token in ("from '../", 'from "../', "from '@/", "from 'element-plus", "from 'vue'"):
                self.assertNotIn(token, text,
                                 f"frontend-shared/src/{name} 必须保持自洽（发现 {token!r}）")


if __name__ == "__main__":
    unittest.main()
