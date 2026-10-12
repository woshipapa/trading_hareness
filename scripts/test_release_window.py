"""No-restart windows as data, and every restarting release script asking them first."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "scripts" / "release_window.py"
OWNER = ("owner-quant-research", "owner-scheduler")


def check(clock: str, *surfaces: str, override: str | None = None) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "RELEASE_CLOCK": clock}
    env.pop("RELEASE_WINDOW_OVERRIDE", None)
    if override:
        env["RELEASE_WINDOW_OVERRIDE"] = override
    return subprocess.run([sys.executable, str(TOOL), "check", *surfaces], env=env,
                          capture_output=True, text=True, check=False)


class ReleaseWindowTests(unittest.TestCase):
    def test_the_owner_windows_and_their_edges(self) -> None:
        for clock, code in (("4 0829", 0), ("4 0830", 3), ("4 1130", 3), ("4 1131", 0), ("4 1259", 0), ("4 1300", 3),
                            ("4 1510", 3), ("4 1511", 0),
                            ("4 1844", 0), ("4 1845", 3), ("4 2205", 3), ("4 2206", 0), ("6 1000", 0), ("7 1900", 0)):
            self.assertEqual(check(clock, *OWNER).returncode, code, clock)

    def test_the_edge_adapter_is_known_and_bound_by_no_window(self) -> None:
        # feishu-relay is its own release unit (decision 0014): every clock an
        # owner surface is refused at must let the edge adapter through.
        for clock in ("2 0830", "2 1000", "2 1130", "2 1200", "2 1300", "2 1400", "2 1510", "2 1900", "2 2100", "6 1000"):
            result = check(clock, "edge-adapter")
            self.assertEqual(result.returncode, 0, clock)
            self.assertEqual(result.stderr, "", clock)

    def test_an_unrestricted_surface_does_not_open_the_window_for_an_owner_surface(self) -> None:
        result = check("2 1000", "edge-adapter", "owner-quant-research")
        self.assertEqual(result.returncode, 3)
        self.assertIn("morning-session", result.stderr)

    def test_no_surface_is_both_windowed_and_unrestricted(self) -> None:
        config = json.loads((ROOT / "config" / "release-windows.json").read_text(encoding="utf-8"))
        windowed = {surface for window in config["windows"] for surface in window["surfaces"]}
        unrestricted = {item["surface"] for item in config.get("unrestricted", [])}
        self.assertEqual(windowed & unrestricted, set())
        self.assertEqual(unrestricted, {"edge-adapter"})
        self.assertTrue(all(item.get("why") for item in config["unrestricted"]))

    def test_a_refusal_names_the_window(self) -> None:
        result = check("1 0900", *OWNER)
        self.assertIn("no-restart window: morning-session 08:30-11:30", result.stderr)

    def test_an_override_must_name_the_window_it_passes(self) -> None:
        self.assertEqual(check("4 1900", *OWNER, override="afternoon-session").returncode, 3)
        passed = check("4 1900", *OWNER, override="post-close-scheduler")
        self.assertEqual(passed.returncode, 0)
        self.assertIn("WARNING", passed.stderr)

    def test_an_unknown_surface_is_a_usage_error(self) -> None:
        self.assertEqual(check("6 1000", "owner-quant-reserch").returncode, 2)


class RestartingScriptsAskTheWindowTests(unittest.TestCase):
    def test_each_script_checks_its_surfaces(self) -> None:
        expected = {
            "scripts/shared-peer/deploy-full-release.sh": "check owner-quant-research owner-scheduler",
            "scripts/shared-peer/deploy-code-only.sh": "check owner-quant-research owner-scheduler",
            "feishu-relay/scripts/edge/deploy-feishu-relay-edge-release.sh": "check edge-adapter",
            "feishu-relay/scripts/edge/hotfix-feishu-relay-edge.sh": "check edge-adapter",
        }
        for script, call in expected.items():
            text = (ROOT / script).read_text(encoding="utf-8")
            self.assertIn(f"scripts/release_window.py\" {call} || exit $?", text, script)


if __name__ == "__main__":
    unittest.main()
