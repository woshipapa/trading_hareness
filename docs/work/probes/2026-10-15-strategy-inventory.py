#!/usr/bin/env python3
"""Read-only inventory of the checked-in strategy contracts and their modules.

This probe intentionally does not open a database or call a provider.  The
owner API snapshots for the live catalog, board and promotion gate are saved
beside this file by the session commands.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
import sys

sys.path.insert(0, str(ROOT / "quant-service"))
from app.platform.strategy_registry import STRATEGY_CONTRACTS  # noqa: E402


def branch_inventory() -> list[dict[str, object]]:
    names = subprocess.check_output(
        ["git", "for-each-ref", "--format=%(refname:short)", "refs/heads"],
        cwd=ROOT,
        text=True,
    ).splitlines()
    rows: list[dict[str, object]] = []
    needles = ("leader", "teacher", "strategy", "tactic", "xiaojie", "pr1", "peer-observe")
    paths = (
        "quant-service/app/dragon_leader_research.py",
        "quant-service/app/teacher_review_rules.py",
        "quant-service/app/xiaojie_leader_flow.py",
        "quant-service/app/ten_day_leader_rotation_research.py",
        "quant-service/app/longhu_multifactor_shadow.py",
        "quant-service/app/strategy_registry.py",
    )
    for name in names:
        if not any(needle in name.lower() for needle in needles):
            continue
        changed = subprocess.check_output(
            ["git", "diff", "--name-only", "main..." + name, "--", *paths],
            cwd=ROOT,
            text=True,
        ).splitlines()
        rows.append({"branch": name, "strategy_files_changed_vs_main": changed})
    return rows


def main() -> None:
    contracts = []
    for item in sorted(STRATEGY_CONTRACTS.values(), key=lambda entry: entry.key):
        contracts.append(
            {
                "key": item.key,
                "model_version": item.model_version,
                "owner_module": item.owner_module,
                "owner_module_exists": (ROOT / "quant-service" / item.owner_module).exists(),
                "runtime_owner": item.runtime_owner,
                "maturity": item.maturity,
                "live_effect": item.live_effect,
                "alert_effect": item.alert_effect,
                "deprecated": bool(item.deprecated_reason),
            }
        )
    print(json.dumps({"contract_count": len(contracts), "contracts": contracts, "branches": branch_inventory()}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
