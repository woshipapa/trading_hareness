"""peer 上的 ops 命令要从容器正在运行的那份源码里跑。

以前写死 ``/app/hotfix/current``：code-only 发布后它存在，全量发布后源码烤进镜像、
overlay 是空的，``docker exec -w`` 直接 exit 127。2026-10-08 那期复盘因此一直建不出
策略包，每 20 分钟空转一轮。
"""
from __future__ import annotations

import importlib.util
import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

DRIVER = Path(__file__).resolve().with_name("teacher_review_daily.py")


def _driver():
    spec = importlib.util.spec_from_file_location("teacher_review_daily", DRIVER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PeerSourceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.driver = _driver()
        self.tmp = Path(tempfile.mkdtemp())
        self.image = self.tmp / "image"
        (self.image / "app").mkdir(parents=True)
        (self.image / "app" / "main.py").write_text("")
        self.overlay = self.tmp / "hotfix" / "current"
        # A stand-in interpreter that reports where it was started and with what.
        fake = self.tmp / "bin" / "python"
        fake.parent.mkdir()
        fake.write_text('#!/bin/sh\necho "cwd=$(pwd -P) path=$PYTHONPATH args=$*"\n')
        fake.chmod(fake.stat().st_mode | stat.S_IXUSR)

    def _run(self, *args: str) -> subprocess.CompletedProcess:
        script = self.driver.peer_source_script(str(self.overlay), str(self.image))
        env = {**os.environ, "PATH": f"{self.tmp / 'bin'}{os.pathsep}{os.environ['PATH']}"}
        return subprocess.run(["sh", "-c", script, "sh", *args], capture_output=True, text=True, env=env)

    def test_full_release_runs_the_image_source(self) -> None:
        done = self._run("status")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn(f"cwd={self.image.resolve()} path={self.image} args=-m app.teacher_review_ops status", done.stdout)

    def test_code_only_release_runs_the_overlay(self) -> None:
        (self.overlay / "app").mkdir(parents=True)
        (self.overlay / "app" / "main.py").write_text("")
        done = self._run("context", "--date=2026-10-08")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn(f"cwd={self.overlay.resolve()} path={self.overlay} ", done.stdout)
        self.assertIn("args=-m app.teacher_review_ops context --date=2026-10-08", done.stdout)

    def test_dangling_overlay_falls_back_to_the_image(self) -> None:
        self.overlay.parent.mkdir(parents=True)
        self.overlay.symlink_to(self.tmp / "releases" / "gone")
        done = self._run("status")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn(f"cwd={self.image.resolve()} ", done.stdout)

    def test_remote_line_does_not_pin_a_working_directory(self) -> None:
        line = self.driver.peer_remote_command(["check", "--pack=-"])
        self.assertNotIn(" -w ", line)
        self.assertIn("/app/hotfix/current", line)
        self.assertIn("|| d=/app;", line)
        self.assertTrue(line.endswith(" sh check --pack=-"))

    def test_unsafe_token_is_refused(self) -> None:
        with self.assertRaises(SystemExit):
            self.driver.peer_remote_command(["status", "x; rm -rf /"])


if __name__ == "__main__":
    unittest.main()
