import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).with_name("tdx-owner-probe.sh")
ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("probe_tdx_routes", Path(__file__).with_name("probe-tdx-routes.py"))
ROUTES = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ROUTES)

CONTAINER = "trading-hareness-peer-quant-research-1"
#: The interpreter of the fake container refuses every name lookup and connection, so that the probe stays offline.
OFFLINE = """import socket


def refuse(*_args, **_kwargs):
    raise OSError("the offline test refuses the network")


socket.getaddrinfo = refuse
socket.socket.connect = refuse
"""
#: The owner's login: `bash -s` reads the script from stdin.
FAKE_SSH = '#!/bin/sh\nprintf "%s\\n" "$@" > "$SSH_ARGV"\nexec bash -s\n'
#: A rootless docker whose exec runs `python -B -` in a working directory that holds the image's app package. It runs
#: nothing before it has seen that the interpreter refuses the network.
FAKE_DOCKER = f"""#!/bin/sh
printf '%s\\n' "$DOCKER_HOST" "$XDG_RUNTIME_DIR" "$@" > "$DOCKER_ARGV"
[ "$1 $2 $3 $4 $5 $6" = "exec -i {CONTAINER} python -B -" ] || exit 97
shift 6
PYTHONPATH="$OFFLINE_DIR" "$PYTHON" -B -c 'import socket
try:
    socket.create_connection(("127.0.0.1", 9), timeout=1)
except OSError as error:
    raise SystemExit(0 if "offline test" in str(error) else 98)
raise SystemExit(98)' || exit 98
cd "$APP_DIR" && PYTHONPATH="$OFFLINE_DIR" exec "$PYTHON" -B - "$@"
"""


class OwnerProbeTests(unittest.TestCase):
    def test_missing_environment_names_only(self):
        result = subprocess.run(["bash", str(SCRIPT)], env={"PATH": os.environ.get("PATH", "")}, capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn("LONGHU_SSH_HOST", result.stderr)

    def test_the_driver_runs_in_the_container_and_imports_the_app_package_there(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            bin_dir, offline_dir = root / "bin", root / "offline"
            bin_dir.mkdir()
            offline_dir.mkdir()
            (offline_dir / "sitecustomize.py").write_text(OFFLINE, encoding="utf-8")
            for name, body in (("ssh", FAKE_SSH), ("docker", FAKE_DOCKER)):
                (bin_dir / name).write_text(body, encoding="utf-8")
                (bin_dir / name).chmod(0o755)
            env = {key: value for key, value in os.environ.items()
                   if key not in ("XDG_RUNTIME_DIR", "DOCKER_HOST", "PYTHONPATH", "PYTHONDONTWRITEBYTECODE")}
            env.update(PATH=f"{bin_dir}{os.pathsep}{env.get('PATH', '')}", LONGHU_SSH_HOST="owner-host",
                       LONGHU_SSH_PORT="22", LONGHU_SSH_USER="owner-user", LONGHU_SSH_KEY_PATH="/secret/key",
                       SSH_ARGV=str(root / "ssh-argv"), DOCKER_ARGV=str(root / "docker-argv"),
                       APP_DIR=str(ROOT / "quant-service"), OFFLINE_DIR=str(offline_dir), PYTHON=sys.executable)
            output = root / "matrix.json"
            result = subprocess.run(["bash", str(SCRIPT), "--profile", "login_one", "--require", "quotes",
                                     "--min-usable-hosts", "0", "--output", str(output)],
                                    env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            for secret in ("/secret/key", "owner-host", "owner-user"):
                self.assertNotIn(secret, result.stdout + result.stderr)
            matrix = json.loads(output.read_text(encoding="utf-8"))
            ssh_argv = (root / "ssh-argv").read_text(encoding="utf-8").split("\n")[:-1]
            docker_argv = (root / "docker-argv").read_text(encoding="utf-8").split("\n")[:-1]
        self.assertEqual(ssh_argv[-3:], ["owner-user@owner-host", "bash", "-s"], "the call lands in `bash -s`")
        self.assertIn("BatchMode=yes", ssh_argv)
        self.assertEqual(docker_argv[:2], [f"unix:///run/user/{os.getuid()}/docker.sock", f"/run/user/{os.getuid()}"],
                         "rootless docker is set up on the owner")
        self.assertEqual(docker_argv[2:], ["exec", "-i", CONTAINER, "python", "-B", "-", "--hosts-file", "embedded",
                                           "--egress", "owner", "--profile", "login_one", "--require", "quotes",
                                           "--min-usable-hosts", "0"])
        self.assertEqual((matrix["schema"], matrix["egress"], matrix["profile"], matrix["require"]),
                         ("tdx-route-matrix-v2", "owner", "login_one", ["quotes"]))
        candidates = ROUTES.read_hosts([ROOT / "scripts/data/tdx_host_candidates.txt",
                                        ROOT / "scripts/data/tdx_host_candidates_other.txt"])
        results = matrix["samples"][0]["results"]
        self.assertEqual([(row["host"], row["port"]) for row in results], candidates, "the candidate files are embedded")
        self.assertEqual({(row["usable"], row["connect_error"]) for row in results}, {(False, "OSError")},
                         "the interpreter of the container tried every host and was refused")

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
