from __future__ import annotations

import subprocess
import sys
import unittest


class FaultStackTests(unittest.TestCase):
    def test_app_import_enables_faulthandler_in_served_process(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "import faulthandler; faulthandler.disable(); import app.main; "
                "assert faulthandler.is_enabled()",
            ],
            capture_output=True,
            text=True,
        )

        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
