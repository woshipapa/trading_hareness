import json
import importlib.util
import io
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

SCRIPT = Path(__file__).with_name("probe-tdx-routes.py")
SPEC = importlib.util.spec_from_file_location("probe_tdx_routes", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class ProbeRoutesTests(unittest.TestCase):
    def test_read_hosts_deduplicates_repeated_files(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "hosts.txt"
            path.write_text("# synthetic\n1.2.3.4:7709\n1.2.3.4:7709\n", encoding="utf-8")
            self.assertEqual(MODULE.read_hosts([path, path]), [("1.2.3.4", 7709)])

    def test_probe_output_and_threshold_use_injected_function(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "hosts.txt"
            path.write_text("1.2.3.4:7709\n", encoding="utf-8")
            def fake(host, port, timeout, profile, required, hist_date):
                return {"host": host, "port": port, "connect_ms": 1, "profile": profile, "commands": {name: {"rows": 1, "usable": True} for name in required}, "usable": True}
            output, error = io.StringIO(), io.StringIO()
            with redirect_stdout(output), redirect_stderr(error):
                code = MODULE.main(["--hosts-file", str(path), "--require", "quotes", "--output", str(Path(root) / "out.json")], fake)
            self.assertEqual(code, 0)
            payload = json.loads(output.getvalue())
            self.assertEqual(payload["schema"], "tdx-route-matrix-v2")
            self.assertIn("quotes:1", error.getvalue())

    def test_default_hist_date_uses_previous_weekday(self):
        self.assertEqual(MODULE.default_hist_date(__import__("datetime").date(2026, 10, 12)).isoformat(), "2026-10-09")


if __name__ == "__main__":
    unittest.main()
