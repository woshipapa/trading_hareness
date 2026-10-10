#!/usr/bin/env python3
"""Generate scripts/CATALOG.md: what each script is for, who owns it, where it runs.

scripts/ holds 140-odd files for three components, five hosts and CI, at one
level and in a few folders, so finding the right one meant reading them.
RULES below classify every tracked script (first match wins); the purpose is
the first line of each script's own docstring or header comment. ``--check``
fails when the catalog is stale or a script matches no rule, so a new script
arrives with its owner and host declared. Moving scripts into owner folders
can then happen one owner at a time.
"""

from __future__ import annotations

import argparse
import ast
from fnmatch import fnmatch
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "scripts" / "CATALOG.md"

# (pattern under scripts/, group, owner component, where it runs)
RULES: list[tuple[str, str, str, str]] = [
    # retired: never run (RELEASE_SYNC_47 iron rules; Tushare retired 2026-10-08)
    ("deploy-intraday-edge-release.sh", "retired", "quant-research (edge intraday, retired)", "never run"),
    ("pull-intraday-edge-evidence.sh", "retired", "quant-research (edge intraday, retired)", "never run"),
    ("verify-intraday-edge-live-session.sh", "retired", "quant-research (edge intraday, retired)", "never run"),
    ("verify-edge-export-grants.mjs", "retired", "quant-research (edge intraday, retired)", "never run"),
    ("sync-watchlist-to-edge.sh", "retired", "quant-research (edge intraday, retired)", "never run"),
    ("converge-quant-edge-sync-monitor-workflow.sh", "retired", "quant-research (edge intraday, retired)", "never run"),
    ("deploy-server.sh", "retired", "platform (compose.server.yaml template)", "no host runs it"),
    # forwarders kept at their old paths
    ("deploy-edge-relay-workflows.sh", "forwarders", "feishu-relay", "workstation"),
    ("deploy-feishu-relay-edge-release.sh", "forwarders", "feishu-relay", "workstation"),
    ("export-edge-relay-workflows.sh", "forwarders", "feishu-relay", "workstation"),
    ("verify-edge-relay-workflows.sh", "forwarders", "feishu-relay", "workstation"),
    ("failback-feishu-relay-to-remote.sh", "forwarders", "feishu-relay", "workstation"),
    ("failover-feishu-relay-to-local.sh", "forwarders", "feishu-relay", "workstation"),
    ("hotfix-feishu-relay-edge.sh", "forwarders", "feishu-relay", "workstation"),
    ("install-edge-import-watchdog.sh", "forwarders", "feishu-relay", "workstation"),
    ("preflight-feishu-relay-handoff.sh", "forwarders", "feishu-relay", "workstation"),
    ("deploy-itougu-*.sh", "forwarders", "feishu-relay", "workstation"),
    # releases
    ("release", "release", "platform", "workstation"),
    ("release_plan.py", "release", "platform", "workstation"),
    ("release_window.py", "release", "platform", "workstation"),
    ("collab_branch.py", "release", "platform", "workstation"),
    ("collab_isolated_tests.sh", "release", "platform", "workstation"),
    ("release-sync-status.sh", "release", "platform", "workstation"),
    ("workflow-status.sh", "release", "platform", "workstation"),
    ("sync_frontend_shared.py", "CI and checks", "platform", "workstation"),
    ("serve_service_index.py", "workstation services", "platform", "workstation"),
    ("shared-peer/deploy-*.sh", "release", "quant-research", "workstation"),
    ("shared-peer/classify-owner-paths.sh", "release", "quant-research", "workstation"),
    ("shared-peer/package-peer-release.sh", "release", "quant-research", "workstation"),
    ("edge/deploy-quant-console-edge.sh", "release", "quant-research (dashboard)", "workstation"),
    ("deploy-local-research-release.sh", "release", "quant-research", "workstation"),
    ("hotfix-quant-service.sh", "release", "quant-research", "workstation"),
    ("preflight-quant-handoff.sh", "release", "quant-research", "workstation"),
    ("quant-opening-preflight.sh", "release", "quant-research", "workstation"),
    ("verify-release-provenance.mjs", "release", "platform", "workstation"),
    ("verify-ten-day-shadow-release.mjs", "release", "quant-research", "workstation"),
    ("verify-zero-downtime-handoff.mjs", "release", "quant-research", "workstation"),
    # owner runtime and its Windows workstation
    ("peer-session-guard.sh", "owner runtime", "quant-research", "47owner"),
    ("shared-peer/release-lock.sh", "owner runtime", "quant-research", "47owner"),
    ("shared-peer/activate-peer-release.sh", "owner runtime", "quant-research", "47owner"),
    ("shared-peer/ensure-batch-tunnel.sh", "owner runtime", "quant-research", "47owner"),
    ("shared-peer/configure-peer-self-tunnel.sh", "owner runtime", "quant-research", "47owner"),
    ("shared-peer/export-peer-data.sh", "owner runtime", "quant-research", "47owner"),
    ("shared-peer/provision-lightserver-rootless.sh", "owner runtime", "quant-research", "47owner"),
    ("shared-peer/verify-*.py", "owner runtime", "quant-research", "47owner"),
    ("cutover/*", "owner runtime", "quant-research", "47owner (inside the container)"),
    ("shared-peer/start-local-longhu-tunnel.sh", "workstation services", "quant-research", "workstation"),
    ("shared-peer/*.ps1", "owner Windows", "quant-research", "owner-windows"),
    ("windows/*.ps1", "owner Windows", "quant-research", "owner-windows"),
    ("deploy-stock-dashboard.ps1", "owner Windows", "quant-research (stock-brain dashboard)", "owner-windows"),
    # workstation services and the teacher cycle
    ("svc_supervisor.py", "workstation services", "platform", "workstation"),
    ("install-svc-baseline.sh", "workstation services", "platform", "workstation"),
    ("unify-service-venv.sh", "workstation services", "platform", "workstation"),
    ("consolidate-services.sh", "workstation services", "platform", "workstation"),
    ("start-compose.sh", "workstation services", "platform", "workstation"),
    ("b300_collab_watch.py", "workstation services", "platform", "workstation"),
    ("b300_collab_dashboard.py", "workstation services", "platform", "workstation"),
    ("owner_collab_watch.py", "workstation services", "platform", "workstation"),
    ("*wechat-image-relay*", "workstation services", "feishu-relay (ingest)", "workstation"),
    ("sync-xiaojie-message-features.sh", "workstation services", "quant-research", "workstation"),
    ("teacher_*.py", "teacher review", "quant-research (teacher review)", "workstation"),
    # local n8n and workflows
    ("converge-*.sh", "local n8n", "quant-research", "workstation"),
    ("*-workflow*.mjs", "local n8n", "quant-research", "workstation"),
    ("hot-publish-quant-workflows.mjs", "local n8n", "quant-research", "workstation"),
    ("export-n8n-workflow-source.sh", "local n8n", "platform", "workstation"),
    ("reconcile-stale-n8n-executions.sh", "local n8n", "platform", "workstation"),
    ("fix-text-content-time-provenance.mjs", "local n8n", "feishu-relay", "workstation"),
    ("recall-xiaolan-backfill.mjs", "local n8n", "feishu-relay", "workstation"),
    ("backup-postgres-and-workflows.sh", "local n8n", "platform", "workstation"),
    ("audit-live-quant-callers.sh", "local n8n", "quant-research", "workstation"),
    # CI and contract checks
    ("verify_*.py", "CI and checks", "platform", "workstation and CI"),
    ("verify-tdx-*.py", "research data", "quant-research", "workstation"),
    ("verify-*", "CI and checks", "platform", "workstation and CI"),
    ("generate-tdx-hosts.py", "research data", "quant-research", "workstation"),
    ("generate*", "CI and checks", "platform", "workstation and CI"),
    ("export_component.py", "CI and checks", "platform", "workstation and CI"),
    # research data tools
    ("marketdata/*", "research data", "quant-research", "workstation (some on the edge)"),
    ("analyst-weekly-review.py", "research data", "quant-research", "workstation"),
    ("audit-fuyao-capabilities.py", "research data", "quant-research", "workstation"),
    ("backfill-*", "research data", "quant-research", "workstation"),
    ("check-provider-health.py", "research data", "quant-research", "workstation"),
    ("fill-*.py", "research data", "quant-research", "quant container"),
    ("import-*.py", "research data", "quant-research", "workstation"),
    ("probe-public-sources.py", "research data", "quant-research", "workstation"),
    ("refresh-watchlist-from-proposals.py", "research data", "quant-research", "workstation"),
    ("run-post-close-pipeline.sh", "research data", "quant-research", "workstation"),
    ("tdx-local-export.py", "research data", "quant-research", "workstation"),
    ("tdx-quant-export.py", "research data", "quant-research", "owner-windows"),
    ("tdx_handshake_experiments.py", "research data", "quant-research", "workstation"),
    ("probe-tdx-routes.py", "research data", "quant-research", "workstation"),
    ("probe-tdx-q-*.py", "research data", "quant-research", "workstation"),
    ("tdx-owner-probe.sh", "research data", "quant-research", "workstation"),
    ("tdx-promote.py", "research data", "quant-research", "workstation"),
]
GROUP_ORDER = ["release", "owner runtime", "owner Windows", "workstation services", "teacher review", "local n8n",
               "research data", "CI and checks", "forwarders", "retired"]


def tracked_scripts() -> list[str]:
    listed = subprocess.run(["git", "ls-files", "scripts"], cwd=ROOT, capture_output=True, text=True, check=True).stdout
    return sorted(name.removeprefix("scripts/") for name in listed.split()
                  if not re.search(r"(^|/)test_|\.test\.mjs$|__pycache__|\.md$|(^|/)__init__\.py$|^scripts/data/", name))


def purpose(path: Path) -> str:
    text = path.read_text(encoding="utf-8", errors="replace")
    if path.suffix == ".py":
        try:
            doc = ast.get_docstring(ast.parse(text))
            if doc:
                return doc.strip().splitlines()[0]
        except SyntaxError:
            pass
    lines = text.splitlines()[:30]
    if any(re.search(r'^exec ".*feishu-relay/scripts', line) for line in lines):
        return "forwards to the copy under feishu-relay/scripts/"
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#!") or "-*-" in stripped:
            continue
        if stripped.startswith(("#", "//")):
            comment = stripped.lstrip("#/ ").strip()
            if len(comment) > 8 and not comment.lower().startswith(("eslint", "requires", "usage")):
                return comment
    return ""


def classify(name: str) -> tuple[str, str, str] | None:
    return next(((group, owner, host) for pattern, group, owner, host in RULES if fnmatch(name, pattern)), None)


def render() -> tuple[str, list[str]]:
    rows: dict[str, list[str]] = {}
    unclassified = []
    for name in tracked_scripts():
        found = classify(name)
        if found is None:
            unclassified.append(name)
            continue
        group, owner, host = found
        text = purpose(ROOT / "scripts" / name).replace("|", "/")
        rows.setdefault(group, []).append(f"| `{name}` | {owner} | {host} | {text[:110]} |")
    out = ["# Scripts catalog", "",
           "> Generated by `python3 scripts/generate_scripts_catalog.py`; do not edit by hand. A new script needs a rule",
           "> in that file (owner component and host). Tests (`test_*.py`, `*.test.mjs`) and the probe data under",
           "> `scripts/data/` are not listed.", ""]
    for group in GROUP_ORDER:
        if group in rows:
            out += [f"## {group} ({len(rows[group])})", "", "| Script | Owner | Runs on | Purpose |", "| --- | --- | --- | --- |",
                    *rows[group], ""]
    return "\n".join(out), unclassified


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    text, unclassified = render()
    if unclassified:
        print("unclassified scripts (add a rule to scripts/generate_scripts_catalog.py):\n  " + "\n  ".join(unclassified),
              file=sys.stderr)
        return 1
    if args.check:
        if not OUTPUT.exists() or OUTPUT.read_text(encoding="utf-8") != text:
            print("scripts/CATALOG.md is stale; run python3 scripts/generate_scripts_catalog.py", file=sys.stderr)
            return 1
        print("scripts catalog is current")
        return 0
    OUTPUT.write_text(text, encoding="utf-8")
    print(f"wrote {OUTPUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
