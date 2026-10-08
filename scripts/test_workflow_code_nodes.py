"""Every Code node in a tracked n8n workflow is valid JavaScript and carries no secrets.

n8n runs a Code node's text as the body of an async function, and only when the
workflow fires, so a syntax error in a committed workflow is found by the first
production run after import. Parse every node here instead. Long node bodies
are business logic that belongs in the owning component; the size budget
keeps new ones from growing.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CODE_NODE = "n8n-nodes-base.code"
# Today's largest node is 3048 characters (the Feishu text-aggregation entry).
SIZE_BUDGET = 3200
SECRET_SHAPES = (
    re.compile(r"open\.feishu\.cn/open-apis/bot/v2/hook/[0-9a-f-]{8,}", re.IGNORECASE),
    re.compile(r"Bearer\s+[A-Za-z0-9._~+/=-]{24,}"),
    re.compile(r"(?i)(api[_-]?key|token|secret|password)\s*[:=]\s*['\"][A-Za-z0-9._~+/=-]{16,}['\"]"),
)


def tracked_workflows() -> list[Path]:
    listed = subprocess.run(["git", "ls-files", "workflows"], cwd=ROOT, capture_output=True, text=True, check=True)
    return [ROOT / name for name in listed.stdout.split() if name.endswith(".json") and not name.endswith("manifest.json")]


def code_nodes() -> list[tuple[str, str, str]]:
    found = []
    for path in tracked_workflows():
        data = json.loads(path.read_text(encoding="utf-8"))
        for workflow in data if isinstance(data, list) else [data]:
            for node in workflow.get("nodes") or []:
                if node.get("type") == CODE_NODE:
                    code = (node.get("parameters") or {}).get("jsCode") or ""
                    found.append((str(path.relative_to(ROOT)), str(node.get("name")), code))
    return found


class WorkflowCodeNodeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.nodes = code_nodes()

    def test_there_are_code_nodes_to_check(self) -> None:
        self.assertGreater(len(self.nodes), 5)

    @unittest.skipUnless(shutil.which("node"), "needs node to parse JavaScript")
    def test_every_code_node_parses_as_an_async_function_body(self) -> None:
        failures = []
        with tempfile.TemporaryDirectory() as directory:
            for index, (path, name, code) in enumerate(self.nodes):
                source = Path(directory) / f"node{index}.js"
                source.write_text(f"(async function () {{\n{code}\n}});\n", encoding="utf-8")
                result = subprocess.run(["node", "--check", str(source)], capture_output=True, text=True)
                if result.returncode:
                    failures.append(f"{path} :: {name}: {result.stderr.strip().splitlines()[-1]}")
        self.assertEqual(failures, [])

    def test_no_code_node_carries_a_secret(self) -> None:
        leaks = [f"{path} :: {name}" for path, name, code in self.nodes
                 if any(shape.search(code) for shape in SECRET_SHAPES)]
        self.assertEqual(leaks, [], "move the value into an n8n credential or an environment variable")

    def test_no_code_node_outgrows_the_budget(self) -> None:
        oversized = [f"{path} :: {name} ({len(code)} chars)" for path, name, code in self.nodes if len(code) > SIZE_BUDGET]
        self.assertEqual(oversized, [], "move the logic into the owning component and keep the node as glue")


if __name__ == "__main__":
    unittest.main()
