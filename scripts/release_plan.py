#!/usr/bin/env python3
"""What releasing one commit involves right now, host by host. Read-only.

    release_plan.py [<target-ref>] [--owner-from <sha>] [--edge-from <sha>] [--owner-db <revision>]

For the owner it says whether the change is a code-only overlay, needs a full
release (and why), or ships nothing; whether the database has every migration
the target needs; and whether the no-restart windows allow it now. For the edge
it says which of the adapter, its workflows and xhs-intel changed since what the
edge runs. The active SHAs and the database revision are read from each host's
/health over ssh unless given; nothing on either host changes.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLASSIFY = ROOT / "scripts" / "shared-peer" / "classify-owner-paths.sh"
WINDOW = ROOT / "scripts" / "release_window.py"
LINEAGE = ROOT / "quant-service" / "scripts" / "migration_lineage.py"
OWNER = (os.environ.get("OWNER_PEER_HOST", "stockpeer@118.178.133.37"), os.environ.get("OWNER_PEER_PORT", "22"),
         os.environ.get("OWNER_PEER_SSH_KEY", str(Path.home() / ".ssh" / "stockpeer_ed25519")))
EDGE = (os.environ.get("RELAY_EDGE_HOST", "root@47.114.113.152"), "22",
        os.environ.get("RELAY_EDGE_SSH_KEY", str(Path.home() / ".ssh" / "feishu_relay_edge_ed25519")))
EDGE_PARTS = {
    "adapter and dashboard": ("feishu-relay/adapter/", "feishu-relay/bridge/", "feishu-relay/dashboard/", "frontend/",
                              "feishu-relay/deploy/edge/"),
    "edge workflows": ("workflows/edge-relay/",),
    "xhs-intel": ("xhs-intel/",),
}


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()


def remote_health(host: tuple[str, str, str], port: int) -> dict:
    """GET /health on the host's loopback; {} when unreachable. Never prints the key path."""
    name, ssh_port, key = host
    try:
        result = subprocess.run(
            ["ssh", "-i", key, "-p", ssh_port, "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", name,
             f"curl -fsS --max-time 20 http://127.0.0.1:{port}/health"],
            capture_output=True, text=True, timeout=60, check=False)
        return json.loads(result.stdout) if result.returncode == 0 else {}
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
        return {}


def resolve(ref: str | None) -> str | None:
    if not ref:
        return None
    try:
        return git("rev-parse", "--verify", f"{ref}^{{commit}}")
    except subprocess.CalledProcessError:
        return None


def window(*surfaces: str) -> str:
    result = subprocess.run([sys.executable, str(WINDOW), "check", *surfaces], capture_output=True, text=True, check=False)
    return "allowed now" if result.returncode == 0 else result.stderr.strip().replace("refusing: ", "not now: ")


def owner_plan(target: str, active: str | None, db_revision: str | None) -> list[str]:
    lines = []
    if not active:
        return ["owner: active release unknown (owner /health unreachable); run `release status` first"]
    if active == target:
        return [f"owner: already at {target[:12]}"]
    rows = [line.split("\t", 1) for line in subprocess.run(
        ["bash", str(CLASSIFY), active, target], cwd=ROOT, capture_output=True, text=True, check=True).stdout.splitlines() if line]
    paths = {kind: [path for row_kind, path in rows if row_kind == kind]
             for kind in ("unclassified", "image", "tunnel-image", "full", "runtime", "migration")}
    label = "owner-<label>"
    head, pending = None, None
    with tempfile.TemporaryDirectory() as directory:
        archive = subprocess.run(["git", "archive", target, "quant-service/migrations/versions"], cwd=ROOT,
                                 capture_output=True, check=False)
        if archive.returncode == 0:
            subprocess.run(["tar", "-x", "-C", directory], input=archive.stdout, check=True)
            versions = str(Path(directory) / "quant-service" / "migrations" / "versions")
            head = subprocess.run([sys.executable, str(LINEAGE), versions, "--head"], capture_output=True, text=True).stdout.strip()
            if db_revision and db_revision != head:
                pending = subprocess.run([sys.executable, str(LINEAGE), versions, "--pending", db_revision],
                                         capture_output=True, text=True).stdout.split()
    behind = head is not None and bool(db_revision) and db_revision != head
    if paths["unclassified"]:
        lines.append(f"owner: NOT RELEASABLE yet ({len(paths['unclassified'])} path(s) have no rule, e.g. "
                     f"{', '.join(paths['unclassified'][:4])})")
        lines.append("  add each to scripts/shared-peer/classify-owner-paths.sh (a script: its rule in "
                     "scripts/generate_scripts_catalog.py), commit, plan again; nothing is built for an unknown path")
    elif paths["image"] or paths["full"] or paths["tunnel-image"]:
        needs = paths["image"] + paths["full"] + paths["tunnel-image"]
        what = "builds the image on the host" if paths["image"] else "ships the host's tree; the image is rebuilt with it"
        lines.append(f"owner: FULL release ({len(needs)} path(s) need it, e.g. {', '.join(needs[:4])}); it {what}")
        lines.append(f"  release owner full {target} {label}            # check")
        lines.append(f"  release owner full {target} {label} --apply    # release")
        if paths["tunnel-image"]:
            lines.append(f"  tunnel image: {', '.join(paths['tunnel-image'])} reach the host's tree with it, but no release "
                         "rebuilds the tunnel image or recreates the tunnels; plan that with the owner")
        if any("catalog row moved" in path for path in paths["full"]):
            lines.append("  a script whose catalog row moved to or from 47owner: if the host's copy is checked to be the "
                         "target's, name its path in RELEASE_RELABELLED_CURRENT and plan again")
    elif paths["runtime"] or paths["migration"]:
        migrations = (f", {len(paths['migration'])} migration file(s): the owner database must be at their head first"
                      if paths["migration"] else "")
        lines.append(f"owner: code-only overlay ({len(paths['runtime'])} runtime file(s){migrations})")
        lines.append(f"  release owner code {target} {label} --from-sha {active} --apply"
                     + ("    # refused until the schema line below is at the head" if behind else ""))
    else:
        lines.append("owner: nothing reaches the owner runtime; a code-only release would only record the SHA")
    if head is not None:
        if not db_revision:
            lines.append(f"  schema: owner database revision unknown; release head {head}")
        elif not behind:
            lines.append(f"  schema: owner database at {head}, as the target needs")
        else:
            lines.append(f"  schema: owner database at {db_revision}, target needs {head}"
                         + (f"; apply first on Windows (stage D): {', '.join(pending)}" if pending else " (not in lineage)"))
    lines.append(f"  window: {window('owner-quant-research', 'owner-scheduler')}")
    return lines


def edge_plan(target: str, active: str | None) -> list[str]:
    if not active:
        return ["edge: active adapter release unknown (edge /health unreachable)"]
    if active == target:
        return [f"edge: already at {target[:12]}"]
    changed = git("diff", "--name-only", active, target).splitlines()
    lines = []
    for part, prefixes in EDGE_PARTS.items():
        paths = [path for path in changed if path.startswith(prefixes)]
        if paths:
            lines.append(f"edge {part}: {len(paths)} changed path(s), e.g. {', '.join(paths[:3])}")
    if not lines:
        return [f"edge: nothing for the edge changed since {active[:12]}"]
    lines.append(f"  adapter image (after CI publishes it): release edge image {target} edge-<label> --apply")
    lines.append(f"  window: {window('edge-adapter')}")
    return lines


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("target", nargs="?", default="origin/main")
    parser.add_argument("--owner-from")
    parser.add_argument("--edge-from")
    parser.add_argument("--owner-db")
    args = parser.parse_args()
    target = resolve(args.target)
    if not target:
        print(f"unknown target {args.target}", file=sys.stderr)
        return 2
    print(f"target {target}  ({git('log', '-1', '--format=%s', target)[:70]})")
    owner_health = {} if args.owner_from else remote_health(OWNER, 15682)
    owner_active = resolve(args.owner_from or (owner_health.get("build") or {}).get("git_sha"))
    db_revision = args.owner_db or ((owner_health.get("owner_storage") or {}).get("database_lineage") or {}).get("alembic_version")
    edge_health = {} if args.edge_from else remote_health(EDGE, 18300)
    edge_active = resolve(args.edge_from or (edge_health.get("build") or {}).get("git_sha"))
    for line in owner_plan(target, owner_active, db_revision) + edge_plan(target, edge_active):
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
