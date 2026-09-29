#!/usr/bin/env python3
import subprocess
import sys
import unittest
from pathlib import Path

from verify_component_runtimes import ROOT, validate_component_runtimes


class ComponentRuntimeTests(unittest.TestCase):
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
