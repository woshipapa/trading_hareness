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
    def test_echo_check_accepts_the_real_two_symbol_answer_and_rejects_a_placeholder(self):
        # Shape of the 60.191.117.167 LOGIN_ONE answer of 2026-10-10 (prices synthetic).
        answer = [{"market": 0, "code": "000001", "price": 11.59}, {"market": 1, "code": "600519", "price": 1450.0}]
        self.assertEqual(MODULE._valid_quote(answer, [(0, "000001"), (1, "600519")]), (True, None))
        # An old BJ code is answered with a placeholder row for 600839 at 0.0 (delta 1c, R1).
        valid, error = MODULE._valid_quote([{"market": 1, "code": "600839", "price": 0.0}], [(2, "832000")])
        self.assertFalse(valid)
        self.assertTrue(error.startswith("code_mismatch"), error)

    def test_handshake_errors_become_host_data(self):
        from app.datasources.sources.tdx_protocol import TdxProtocolError

        class ClosesDuringLogin:
            def __init__(self, *_args, handshake_profile):
                self.handshake_profile = handshake_profile

            def __enter__(self):
                raise TdxProtocolError("TDX server closed the connection")

            def __exit__(self, *_exc):
                return False

        row = MODULE.probe_host("9.9.9.9", 7709, 1.0, "login_one", ["quotes"], None, client_factory=ClosesDuringLogin)
        self.assertEqual((row["usable"], row["connect_error"]), (False, "TdxProtocolError"))

    def test_counts_are_hosts_usable_in_every_sample(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "hosts.txt"
            path.write_text("1.2.3.4:7709\n5.6.7.8:7709\n", encoding="utf-8")
            calls = []

            def flaky(host, port, timeout, handshake_profile, required, hist_date):
                calls.append(host)
                usable = host == "1.2.3.4" or len(calls) <= 2      # 5.6.7.8 fails in the second sample
                return {"host": host, "port": port, "connect_ms": 1, "profile": handshake_profile,
                        "commands": {name: {"rows": 1, "usable": usable} for name in required}, "usable": usable}

            error = io.StringIO()
            with redirect_stdout(io.StringIO()), redirect_stderr(error):
                code = MODULE.main(["--hosts-file", str(path), "--require", "quotes", "--samples", "2",
                                    "--min-usable-hosts", "2", "--threads", "1"], flaky)
        self.assertEqual(code, 2, "only one host is usable in both samples")
        self.assertIn("usable_hosts=1", error.getvalue())
        self.assertIn("usable_by_command=quotes:1", error.getvalue())

    def test_read_hosts_deduplicates_repeated_files(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "hosts.txt"
            path.write_text("# synthetic\n1.2.3.4:7709\n1.2.3.4:7709\n", encoding="utf-8")
            self.assertEqual(MODULE.read_hosts([path, path]), [("1.2.3.4", 7709)])

    def test_probe_output_and_threshold_use_injected_function(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "hosts.txt"
            path.write_text("1.2.3.4:7709\n", encoding="utf-8")
            def fake(host, port, timeout, handshake_profile, required, hist_date):
                return {"host": host, "port": port, "connect_ms": 1, "profile": handshake_profile, "commands": {name: {"rows": 1, "usable": True} for name in required}, "usable": True}
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
