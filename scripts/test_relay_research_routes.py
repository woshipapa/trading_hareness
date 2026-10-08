"""The relay's research routes against the quant route contract and the dashboards.

quant-research's OpenAPI document is the route contract; frontend/src/api/generated.ts
is its checked-in copy, which CI regenerates from the running service. Every upstream
the relay proxies to must be a path and method in it, and every research path a
dashboard calls must be a relay route - otherwise a rename on either side ships as a
404 on the edge.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GENERATED = ROOT / "frontend" / "src" / "api" / "generated.ts"
ROUTES = ROOT / "feishu-relay" / "adapter" / "research-routes.mjs"
DASHBOARDS = (ROOT / "frontend" / "src", ROOT / "feishu-relay" / "dashboard" / "src")
METHODS = ("get", "put", "post", "delete", "patch")


def openapi_operations() -> dict[str, set[str]]:
    """``path -> {METHOD, ...}`` from the generated ``paths`` interface."""
    text = GENERATED.read_text(encoding="utf-8")
    body = text[text.index("export interface paths {"):text.index("\n}\n", text.index("export interface paths {"))]
    operations: dict[str, set[str]] = {}
    for match in re.finditer(r'^    "(/[^"]*)": \{\n(.*?)^    \};', body, flags=re.MULTILINE | re.DOTALL):
        block = match.group(2)
        operations[match.group(1)] = {method.upper() for method in METHODS
                                      if re.search(rf"^        {method}: operations\[", block, flags=re.MULTILINE)}
    return operations


def relay_routes() -> list[dict[str, str]]:
    script = f"import({json.dumps(ROUTES.as_uri())}).then((m) => console.log(JSON.stringify(m.researchRouteTable())))"
    result = subprocess.run(["node", "--input-type=module", "-e", script], capture_output=True, text=True, check=True)
    return json.loads(result.stdout)


def dashboard_research_paths() -> dict[str, list[str]]:
    """Research path literals in the dashboards; `${...}` segments become `{}`."""
    found: dict[str, list[str]] = {}
    for root in DASHBOARDS:
        for path in sorted(root.rglob("*")):
            if path.suffix not in {".ts", ".vue", ".js", ".mjs"} or path == GENERATED:
                continue
            for literal in re.findall(r"""['"`](/api/research/[^'"`?\s]*)""", path.read_text(encoding="utf-8")):
                literal = re.sub(r"\$\{[^}]*\}", "{}", literal)
                if literal.endswith("/"):
                    continue  # a prefix being joined, not a route
                found.setdefault(literal, []).append(str(path.relative_to(ROOT)))
    return found


@unittest.skipUnless(shutil.which("node"), "needs node to read the relay route module")
class RelayResearchRouteContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.operations = openapi_operations()
        cls.routes = relay_routes()

    def test_the_contract_parses(self) -> None:
        self.assertGreater(len(self.operations), 100)
        self.assertIn("GET", self.operations["/health"])

    def served(self, method: str, upstream: str) -> bool:
        """The exact OpenAPI path, or a concrete value of a templated one (universes/core)."""
        if method in self.operations.get(upstream, set()):
            return True
        return any(method in methods and re.fullmatch(re.sub(r"\\\{[a-z_]+\\\}", "[^/]+", re.escape(path)), upstream)
                   for path, methods in self.operations.items() if "{" in path and "{" not in upstream)

    def test_every_upstream_is_a_quant_route_with_that_method(self) -> None:
        missing = [f"{route['method']} {route['path']} -> {route['upstream']}" for route in self.routes
                   if not self.served(route["method"], route["upstream"])]
        self.assertEqual(missing, [], "the relay proxies to routes quant-research does not serve")

    def test_every_dashboard_research_path_is_a_relay_route(self) -> None:
        declared = {re.sub(r"\{[a-z_]+\}", "{}", route["path"]) for route in self.routes}
        unknown = {path: files for path, files in dashboard_research_paths().items() if path not in declared}
        self.assertEqual(unknown, {}, "the dashboards call research paths the relay does not route")


if __name__ == "__main__":
    unittest.main()
