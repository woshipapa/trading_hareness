"""The data-source package stays independent of strategies, and vice versa."""

import ast
import unittest
from pathlib import Path

from app.platform.strategy_registry import STRATEGY_CONTRACTS

APP = Path(__file__).resolve().parents[1] / "app"
PACKAGE = APP / "datasources"

#: Strategy and rule code that must reach data through capabilities.
STRATEGY_CODE = {APP / name for name in (
    "intraday_signal_rules.py", "intraday_scan_preparation.py", "intraday_watchlist_scan_service.py",
    "post_close_strategy_service.py", "watchlist_countertrend_rebound.py", "strategy_pattern_mining_service.py",
    "xiaojie_reference_repository.py", "xiaojie_leader_flow.py", "limit_up_continuation.py",
    "disclosure_day_watch.py", "ten_day_leader_rotation_research.py", "post_close_candidate_screen.py",
)}

#: Application modules the data layer may use: transport, persistence and
#: lease infrastructure plus the pre-existing Fuyao client -- no strategy,
#: rule, router or composition-root code.
ALLOWED_APP_MODULES = frozenset({
    "http_clients", "http_retry", "network_health", "provider_health", "public_market_repository",
    "runtime_leases", "runtime_tasks", "database", "async_market_session_repository", "fuyao_provider",
})


def _imports(path: Path) -> list[tuple[int, str | None, list[str]]]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    result = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            result.append((node.level, node.module, [alias.name for alias in node.names]))
        elif isinstance(node, ast.Import):
            result.extend((0, alias.name, []) for alias in node.names)
    return result


class DataSourceBoundaryTests(unittest.TestCase):
    def test_package_imports_only_infrastructure_from_the_app(self):
        checked = 0
        for path in PACKAGE.rglob("*.py"):
            depth = len(path.relative_to(PACKAGE).parts) - 1   # 0 for files in the package root
            for level, module, names in _imports(path):
                if level == 0:
                    self.assertFalse((module or "").startswith("app"), f"{path}: absolute app import {module}")
                    continue
                if level <= depth + 1:
                    continue   # resolves inside app.datasources
                target = module.split(".")[0] if module else names[0]
                self.assertIn(target, ALLOWED_APP_MODULES, f"{path.relative_to(APP)} imports app.{target}")
                checked += 1
        self.assertGreater(checked, 0)

    def test_strategies_never_import_source_adapters(self):
        strategy_files = {APP.parent / contract.owner_module for contract in STRATEGY_CONTRACTS.values()}
        strategy_files |= {APP / name for name in (
            "xiaojie_reference_repository.py", "intraday_signal_rules.py", "post_close_candidate_screen.py",
            "ten_day_leader_ranking.py", "limit_pool_merge.py", "short_term_review.py",
        )}
        for path in strategy_files:
            source = path.read_text(encoding="utf-8")
            self.assertNotIn("datasources.sources", source, path.name)
            self.assertNotIn("datasources.collectors", source, path.name)

    def test_strategy_code_names_no_vendor(self):
        """String literals in strategy code never name a source or its labels.

        Docstrings and comments may explain history; executable strings may
        not select a vendor -- that choice lives in the catalog and in the
        strategy's declared data needs.
        """
        from app.datasources.catalog import SOURCE_LABELS, SOURCES

        vendor_strings = set(SOURCES) | set(SOURCE_LABELS)
        for path in sorted(STRATEGY_CODE):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            docstrings = {id(node.body[0].value) for node in ast.walk(tree)
                          if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                          and node.body and isinstance(node.body[0], ast.Expr)
                          and isinstance(node.body[0].value, ast.Constant)}
            found = sorted({node.value for node in ast.walk(tree)
                            if isinstance(node, ast.Constant) and isinstance(node.value, str)
                            and id(node) not in docstrings
                            and any(vendor in node.value for vendor in vendor_strings)})
            self.assertEqual(found, [], f"{path.name} names vendors: {found}")

    def test_public_transport_uses_the_shared_pool(self):
        source = (PACKAGE / "http.py").read_text(encoding="utf-8")
        self.assertIn("public_http_client()", source)
        self.assertNotIn("AsyncClient(", source)
        for path in (PACKAGE / "sources").glob("*.py"):
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("import httpx", text, path.name)
            self.assertNotIn("AsyncClient(", text, path.name)

    def test_catalog_and_contracts_are_pure_declarations(self):
        for name in ("contracts.py", "catalog.py"):
            for level, module, _names in _imports(PACKAGE / name):
                self.assertTrue(level == 0 and (module or "") in {"__future__", "dataclasses", "typing"}
                                or level == 1 and module in {"contracts"}, f"{name} imports {module}")


if __name__ == "__main__":
    unittest.main()
