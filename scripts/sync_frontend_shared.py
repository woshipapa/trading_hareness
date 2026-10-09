#!/usr/bin/env python3
"""Sync the frontend-shared canonical sources into each app's vendored copy.

The shared layer is consumed as per-app copies, never as a live package link,
so a canonical change touches no component until that app explicitly syncs —
the two console frontends keep independent builds, images and release timing.
Default is a read-only status report; ``--apply [app...]`` copies the
canonical files into the chosen apps (all apps when none is named).
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CANONICAL = ROOT / "frontend-shared" / "src"

# canonical file (under frontend-shared/src/) -> {app name: vendored copy}
FILES: dict[str, dict[str, str]] = {
    "http.ts": {
        "frontend": "frontend/src/shared/http.ts",
        "feishu-dashboard": "feishu-relay/dashboard/src/shared/http.ts",
    },
}


def apps() -> set[str]:
    return {app for targets in FILES.values() for app in targets}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true", help="copy canonical files into the chosen apps")
    parser.add_argument("app", nargs="*", help=f"apps to sync (default: all of {sorted(apps())})")
    args = parser.parse_args()
    chosen = set(args.app) or apps()
    unknown = chosen - apps()
    if unknown:
        print(f"unknown app(s): {sorted(unknown)}; known: {sorted(apps())}", file=sys.stderr)
        return 2

    stale = 0
    for name, targets in FILES.items():
        source = CANONICAL / name
        if not source.is_file():
            print(f"canonical file missing: {source.relative_to(ROOT)}", file=sys.stderr)
            return 1
        for app, relative in sorted(targets.items()):
            if app not in chosen:
                continue
            target = ROOT / relative
            if target.is_file() and target.read_text(encoding="utf-8") == source.read_text(encoding="utf-8"):
                print(f"in-sync   {app}: {relative}")
                continue
            if args.apply:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
                print(f"synced    {app}: {relative}")
            else:
                state = "missing" if not target.is_file() else "behind canonical"
                print(f"stale     {app}: {relative} ({state}; run --apply {app})")
                stale += 1
    return 1 if stale else 0


if __name__ == "__main__":
    raise SystemExit(main())
