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
            fake.write_text("#!/bin/sh\ncat >/tmp/tdx-owner-probe-input\ngrep -q EMBEDDED_HOSTS_TEXT /tmp/tdx-owner-probe-input\ngrep -q probe_host /tmp/tdx-owner-probe-input\nprintf '{\"synthetic\":true}\\n'\n", encoding="utf-8")
            fake.chmod(0o755)
            env = dict(os.environ, PATH=root + os.pathsep + os.environ.get("PATH", ""), LONGHU_SSH_HOST="synthetic-host", LONGHU_SSH_PORT="22", LONGHU_SSH_USER="synthetic-user", LONGHU_SSH_KEY_PATH="/secret/key")
            result = subprocess.run(["bash", str(SCRIPT), "--profile", "login_one", "--require", "quotes"], env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, '{"synthetic":true}\n')


if __name__ == "__main__":
    unittest.main()
