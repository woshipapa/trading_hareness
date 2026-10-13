"""Tactics and conditions are evidence observations, not strategies (decision 0023).

Nothing checks import direction inside the service, so this pins it: the trial modules read no strategy registry,
no strategy contract carries a tactic key, and only the trial modules, their read routes and the composition root import them.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

from app.platform.strategy_registry import STRATEGY_CONTRACTS
from app.tactic_trials import TACTICS

APP = Path(__file__).resolve().parents[1] / "app"
TRIAL_MODULES = {"tactic_conditions", "tactic_exits", "tactic_trials", "tactic_trial_repository", "tactic_trial_runtime"}
STRATEGY_MODULES = {"strategy_promotion", "strategy_daily_candidate_ledger", "strategy_registry"}
ALLOWED_IMPORTERS = TRIAL_MODULES | {"main", "market_radar_reads"}


def imported_modules(path: Path) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom):
            names.update(filter(None, [(node.module or "").split(".")[-1], *(alias.name for alias in node.names)]))
        elif isinstance(node, ast.Import):
            names.update(alias.name.split(".")[-1] for alias in node.names)
    return names


class TacticTrialBoundaryTests(unittest.TestCase):
    def test_the_trial_modules_read_no_strategy_registry(self):
        for module in sorted(TRIAL_MODULES):
            with self.subTest(module=module):
                self.assertFalse(imported_modules(APP / f"{module}.py") & STRATEGY_MODULES)

    def test_no_strategy_contract_carries_a_tactic_key(self):
        self.assertFalse({tactic.key for tactic in TACTICS} & set(STRATEGY_CONTRACTS))

    def test_only_the_trial_modules_their_routes_and_the_composition_root_import_them(self):
        importers = {path.stem for path in APP.rglob("*.py") if imported_modules(path) & TRIAL_MODULES}
        self.assertEqual(importers - ALLOWED_IMPORTERS, set())


if __name__ == "__main__":
    unittest.main()
