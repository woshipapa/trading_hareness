import os
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).with_name("tdx-owner-probe.sh")


class OwnerProbeTests(unittest.TestCase):
    def test_missing_environment_names_only(self):
        result = subprocess.run(["bash", str(SCRIPT)], env={"PATH": os.environ.get("PATH", "")}, capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn("LONGHU_SSH_HOST", result.stderr)

    def test_fake_ssh_receives_embedded_source_and_returns_json(self):
        with tempfile.TemporaryDirectory() as root:
            fake = Path(root) / "ssh"
            input_path = Path(root) / "input"
            fake.write_text(f"#!/bin/sh\ncat >{input_path}\ngrep -q EMBEDDED_HOSTS_TEXT {input_path}\ngrep -q probe_host {input_path}\nprintf '{{\"synthetic\":true}}\\n'\n", encoding="utf-8")
            fake.chmod(0o755)
            env = dict(os.environ, PATH=root + os.pathsep + os.environ.get("PATH", ""), LONGHU_SSH_HOST="synthetic-host", LONGHU_SSH_PORT="22", LONGHU_SSH_USER="synthetic-user", LONGHU_SSH_KEY_PATH="/secret/key")
            output = Path(root) / "output.json"
            result = subprocess.run(["bash", str(SCRIPT), "--profile", "login_one", "--require", "quotes", "--output", str(output)], env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(output.read_text(), '{"synthetic":true}\n')

    def _run(self, root, ssh_body, *extra):
        fake = Path(root) / "ssh"
        fake.write_text("#!/bin/sh\n" + ssh_body, encoding="utf-8")
        fake.chmod(0o755)
        env = dict(os.environ, PATH=root + os.pathsep + os.environ.get("PATH", ""), LONGHU_SSH_HOST="secret-host",
                   LONGHU_SSH_PORT="22", LONGHU_SSH_USER="secret-user", LONGHU_SSH_KEY_PATH="/secret/key")
        return subprocess.run(["bash", str(SCRIPT), "--output", str(Path(root) / "out.json"), *extra], env=env,
                              capture_output=True, text=True)

    def test_ssh_errors_are_shown_with_connection_values_replaced(self):
        with tempfile.TemporaryDirectory() as root:
            result = self._run(root, "cat >/dev/null\necho 'Load key /secret/key for secret-user@secret-host: denied' >&2\nexit 255\n")
        self.assertEqual(result.returncode, 255)
        self.assertIn("Load key [ssh-key] for [ssh-user]@[ssh-host]: denied", result.stderr, "the error is shown, redacted")
        for secret in ("secret-host", "secret-user", "/secret/key"):
            self.assertNotIn(secret, result.stderr)

    def test_a_sweep_below_its_threshold_keeps_its_matrix_and_exit_code(self):
        with tempfile.TemporaryDirectory() as root:
            result = self._run(root, "cat >/dev/null\nprintf '{\"samples\": []}\\n'\necho 'egress=owner usable_hosts=0' >&2\nexit 2\n")
            saved = (Path(root) / "out.json").read_text(encoding="utf-8")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(saved, '{"samples": []}\n')
        self.assertIn("usable_hosts=0", result.stderr)

if __name__ == "__main__":
    unittest.main()
