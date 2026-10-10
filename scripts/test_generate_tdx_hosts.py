import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).with_name("generate-tdx-hosts.py")


class GenerateTdxHostsTests(unittest.TestCase):
    def test_selects_hosts_available_in_every_sample_and_caps_subnets(self):
        namespace = {}
        namespace["__file__"] = str(SCRIPT)
        exec(SCRIPT.read_text(encoding="utf-8"), namespace)
        payload = {"profile": "login_one", "samples": [
            {"probed_at_utc": "synthetic-1", "results": [
                {"host": "10.0.0.1", "port": 7709, "connect_ms": 4, "usable": True},
                {"host": "10.0.0.2", "port": 7709, "connect_ms": 5, "usable": True},
                {"host": "10.0.0.3", "port": 7709, "connect_ms": 6, "usable": True},
                {"host": "10.0.0.4", "port": 7709, "connect_ms": 1, "usable": True},
            ]},
            {"probed_at_utc": "synthetic-2", "results": [
                {"host": "10.0.0.1", "port": 7709, "connect_ms": 8, "usable": True},
                {"host": "10.0.0.2", "port": 7709, "connect_ms": 9, "usable": True},
                {"host": "10.0.0.3", "port": 7709, "connect_ms": 10, "usable": True},
                {"host": "10.1.0.4", "port": 7709, "connect_ms": 2, "usable": True},
            ]},
        ]}
        selected = namespace["select_hosts"](payload)
        self.assertEqual([host for host, _latency in selected], [("10.0.0.1", 7709), ("10.0.0.2", 7709), ("10.0.0.3", 7709)])
        self.assertEqual(namespace["select_hosts"]({"samples": []}), [])

    def test_old_matrix_is_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            matrix = Path(root) / "old.json"
            matrix.write_text(json.dumps({"date": "synthetic", "results": []}), encoding="utf-8")
            result = subprocess.run([sys.executable, str(SCRIPT), str(matrix)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn("旧三包矩阵不能用于生成", result.stderr)

    def test_check_detects_stale_output(self):
        namespace = {}
        namespace["__file__"] = str(SCRIPT)
        exec(SCRIPT.read_text(encoding="utf-8"), namespace)
        payload = {"profile": "login_one", "samples": [{"probed_at_utc": "synthetic", "results": [{"host": "1.2.3.4", "port": 7709, "connect_ms": 1, "usable": True}]}]}
        with tempfile.TemporaryDirectory() as root:
            output = Path(root) / "hosts.py"
            output.write_text("stale\n", encoding="utf-8")
            matrix = Path(root) / "matrix.json"
            matrix.write_text(json.dumps(payload), encoding="utf-8")
            result = subprocess.run([sys.executable, str(SCRIPT), str(matrix), "--output", str(output), "--check"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)


if __name__ == "__main__":
    unittest.main()
