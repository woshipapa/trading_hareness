#!/usr/bin/env python3
import subprocess
import sys
import unittest
from pathlib import PurePosixPath, PureWindowsPath

from verify_component_runtimes import (
    ROOT,
    _compose_build_contract,
    validate_component_runtimes,
)


class ComponentRuntimeTests(unittest.TestCase):
    def test_compose_paths_are_posix_on_windows_and_linux(self) -> None:
        compose = "build:\n  context: .\n  dockerfile: adapter/Dockerfile.standalone\n"
        for root in (PureWindowsPath("X:/fixture"), PurePosixPath("/fixture")):
            with self.subTest(root=root):
                self.assertTrue(_compose_build_contract(compose,
                    "relay/adapter/Dockerfile.standalone", root / "relay", root))
                self.assertFalse(_compose_build_contract(compose,
                    "relay/other/Dockerfile.standalone", root / "relay", root))

    def test_each_component_has_an_isolated_runtime_contract(self) -> None:
        self.assertEqual(validate_component_runtimes(ROOT), [])

    def test_command_entrypoint_passes(self) -> None:
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "verify_component_runtimes.py"), "--check"],
            cwd=ROOT, capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("component runtime contract check passed", result.stdout)


if __name__ == "__main__":
    unittest.main()
