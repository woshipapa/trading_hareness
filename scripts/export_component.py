#!/usr/bin/env python3
"""Export one logical component as a secret-free standalone project archive.

The repository stays a monorepo while contracts are being stabilized. This
tool makes the future repository move reproducible from the current worktree:
it exports only the component-owned paths, writes a small component manifest,
and refuses common runtime/build artifacts.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
from pathlib import Path
import tarfile
import tempfile

from verify_component_boundaries import ROOT, load_manifest


EXCLUDED_PARTS = frozenset({
    ".git", ".pytest_cache", "__pycache__", "node_modules", "dist", "build",
})
EXCLUDED_NAMES = frozenset({".env", ".env.local", ".env.production", ".DS_Store"})


def _component(manifest: dict, component_id: str) -> dict:
    for item in manifest["components"]:
        if item["id"] == component_id:
            return item
    raise ValueError(f"unknown component: {component_id}")


def _excluded(path: Path) -> bool:
    return bool(EXCLUDED_PARTS.intersection(path.parts)) or path.name in EXCLUDED_NAMES or path.name.endswith(".env")


def _matches(path: str, pattern: str) -> bool:
    if fnmatch.fnmatchcase(path, pattern):
        return True
    if pattern.endswith("/**"):
        return path == pattern[:-3].rstrip("/")
    return False


def component_files(component: dict, root: Path = ROOT) -> list[Path]:
    """Return deterministic files owned by one component in the worktree."""
    candidates: set[Path] = set()
    for pattern in component["paths"]:
        if pattern.endswith("/**") and not any(char in pattern[:-3] for char in "*?["):
            base = root / pattern[:-3].rstrip("/")
            if base.is_dir():
                candidates.update(path for path in base.rglob("*") if path.is_file())
            elif base.is_file():
                candidates.add(base)
            continue
        for path in root.rglob("*"):
            if path.is_file() and _matches(path.relative_to(root).as_posix(), pattern):
                candidates.add(path)
    return sorted(
        path for path in candidates
        if not _excluded(path.relative_to(root))
    )


def export_component(component_id: str, output: Path, *, root: Path = ROOT) -> Path:
    manifest = load_manifest()
    component = _component(manifest, component_id)
    files = component_files(component, root)
    if not files:
        raise ValueError(f"component {component_id} has no exportable files")
    output = output.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    metadata = {
        "schema_version": 1,
        "component": {key: component[key] for key in ("id", "runtime", "release", "release_units", "paths")},
        "source_root": "repository-root",
        "local_manifest": component.get("manifest"),
        "file_count": len(files),
        "excluded": sorted(EXCLUDED_PARTS | EXCLUDED_NAMES),
    }
    with tempfile.TemporaryDirectory(prefix=f"component-export-{component_id}-") as staging:
        stage = Path(staging)
        for path in files:
            relative = path.relative_to(root)
            destination = stage / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(path.read_bytes())
        (stage / "COMPONENT_MANIFEST.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
        )
        with tarfile.open(output, "w:gz") as archive:
            for path in sorted(stage.rglob("*")):
                archive.add(path, arcname=path.relative_to(stage).as_posix(), recursive=False)
    return output


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("component", choices=[item["id"] for item in load_manifest()["components"]])
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    path = export_component(args.component, args.output)
    print(f"exported {args.component}: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
