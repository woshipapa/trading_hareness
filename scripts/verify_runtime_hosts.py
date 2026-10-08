#!/usr/bin/env python3
"""Check config/components.json ``hosts``: every place this system runs is declared.

Before 2026-10-08 the manifest knew three components and two hosts. The owner's
Windows workstation (the database itself, plus a second quant API) and the
operator's Mac (whose launchd supervisor writes to the owner) were nowhere, and
two Mac tasks had been failing against a retired edge writer every five minutes
for weeks without anyone noticing.

Rules:

* every component's ``runtime`` is a declared host that lists the component;
* every supervisor task on the workstation is classified, so a new task that
  writes to the owner cannot appear unannounced;
* a task classified ``retired`` must not start by default.
"""

from __future__ import annotations

import argparse
import fnmatch
import importlib.util
import os
import sys
from pathlib import Path
from typing import Callable, Iterable

from verify_component_boundaries import ROOT, load_manifest

TASK_CLASSES = {"owner-writer", "owner-reader", "edge-client", "tunnel", "local", "retired"}
HOST_FIELDS = ("id", "role", "components", "processes", "release", "credentials", "writes")
SUPERVISOR = ROOT / "scripts" / "svc_supervisor.py"
#: Flags that switch optional supervisor tasks on; all set means "every task that can exist".
OPTIONAL_TASK_FLAGS = ("ITOUGU_TABLE_WATCH", "WATCHLIST_EDGE_SYNC")


def supervisor_task_names(*, all_optional: bool) -> set[str]:
    """Task names the workstation supervisor would start, with or without the optional flags."""
    saved = {flag: os.environ.get(flag) for flag in OPTIONAL_TASK_FLAGS}
    try:
        for flag in OPTIONAL_TASK_FLAGS:
            if all_optional:
                os.environ[flag] = "1"
            else:
                os.environ.pop(flag, None)
        spec = importlib.util.spec_from_file_location("svc_supervisor_snapshot", SUPERVISOR)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return {task["name"] for task in module.TASKS}
    finally:
        for flag, value in saved.items():
            if value is None:
                os.environ.pop(flag, None)
            else:
                os.environ[flag] = value


def _classify(name: str, mapping: dict[str, str]) -> str | None:
    if name in mapping:
        return mapping[name]
    for pattern, value in mapping.items():
        if any(char in pattern for char in "*?[") and fnmatch.fnmatchcase(name, pattern):
            return value
    return None


def host_violations(manifest: dict, task_names: Callable[..., Iterable[str]] = supervisor_task_names) -> list[str]:
    hosts = manifest.get("hosts")
    if not isinstance(hosts, list) or not hosts:
        return ["config/components.json has no hosts section"]
    violations: list[str] = []
    by_id: dict[str, dict] = {}
    for host in hosts:
        missing = [field for field in HOST_FIELDS if field not in host]
        if missing:
            violations.append(f"host {host.get('id', '?')} is missing {missing}")
            continue
        if host["id"] in by_id:
            violations.append(f"host {host['id']} is declared twice")
        by_id[host["id"]] = host
    component_ids = {component["id"] for component in manifest.get("components") or []}
    for component in manifest.get("components") or []:
        host = by_id.get(component.get("runtime"))
        if host is None:
            violations.append(f"component {component['id']} runs on undeclared host {component.get('runtime')!r}")
        elif component["id"] not in host["components"]:
            violations.append(f"host {host['id']} does not list component {component['id']}, which runs there")
    for host in by_id.values():
        for name in host["components"]:
            if name not in component_ids:
                violations.append(f"host {host['id']} lists unknown component {name}")
    workstations = [host for host in by_id.values() if "supervisor_tasks" in host]
    if len(workstations) != 1:
        violations.append("exactly one host must classify the workstation supervisor_tasks")
        return violations
    mapping = workstations[0]["supervisor_tasks"]
    for pattern, value in mapping.items():
        if value not in TASK_CLASSES:
            violations.append(f"supervisor task {pattern} has unknown class {value!r}; use one of {sorted(TASK_CLASSES)}")
    for name in sorted(task_names(all_optional=True)):
        if _classify(name, mapping) is None:
            violations.append(
                f"supervisor task {name} is not classified in hosts[{workstations[0]['id']}].supervisor_tasks; "
                "say whether it writes to the owner, reads it, talks to the edge or stays local")
    for name in sorted(task_names(all_optional=False)):
        if _classify(name, mapping) == "retired":
            violations.append(f"supervisor task {name} is classified retired but starts by default")
    return violations


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true")
    parser.parse_args()
    violations = host_violations(load_manifest())
    if violations:
        print("runtime host check failed:", *violations, sep="\n- ")
        return 1
    print("runtime host check passed")
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    raise SystemExit(main())
