#!/usr/bin/env python3
"""Validate the repository's logical component map and report impacted units.

The repository remains a monorepo while Feishu, Quant and XHS move toward
independent projects. This check makes the intended ownership explicit before
physical repository moves introduce a harder-to-debug release split.
"""

from __future__ import annotations

import argparse
import ast
import fnmatch
import json
from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "config" / "components.json"


def load_manifest() -> dict:
    document = json.loads(MANIFEST.read_text(encoding="utf-8"))
    if document.get("schema_version") != 1:
        raise ValueError("config/components.json has an unsupported schema_version")
    components = document.get("components")
    if not isinstance(components, list) or not components:
        raise ValueError("config/components.json must define components")
    ids = [item.get("id") for item in components]
    if any(not isinstance(item, str) or not item for item in ids):
        raise ValueError("every component needs a non-empty id")
    if len(set(ids)) != len(ids):
        raise ValueError("component ids must be unique")
    for item in components:
        paths = item.get("paths")
        if not isinstance(paths, list) or not paths or any(not isinstance(path, str) for path in paths):
            raise ValueError(f"component {item['id']} needs non-empty string paths")
        if not item.get("runtime") or not item.get("release"):
            raise ValueError(f"component {item['id']} needs runtime and release metadata")
        release_units = item.get("release_units")
        if (not isinstance(release_units, list) or not release_units
                or any(not isinstance(unit, str) or not unit for unit in release_units)):
            raise ValueError(f"component {item['id']} needs non-empty release_units")
        component_manifest = item.get("manifest")
        if not isinstance(component_manifest, str) or not component_manifest:
            raise ValueError(f"component {item['id']} needs a local manifest path")
        manifest_path = ROOT / component_manifest
        if not manifest_path.is_file():
            raise ValueError(f"component {item['id']} local manifest is missing: {component_manifest}")
        try:
            local = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError(f"component {item['id']} local manifest is invalid: {error}") from error
        if local.get("schema_version") != 1 or local.get("id") != item["id"]:
            raise ValueError(f"component {item['id']} local manifest has wrong schema or id")
        for key in ("runtime", "release", "release_units", "interfaces", "external_dependencies", "commands"):
            if key not in local:
                raise ValueError(f"component {item['id']} local manifest is missing {key}")
        if local["runtime"] != item["runtime"] or local["release"] != item["release"]:
            raise ValueError(f"component {item['id']} local manifest release metadata disagrees with global map")
        if local["release_units"] != item["release_units"]:
            raise ValueError(f"component {item['id']} local manifest release units disagree with global map")
        if not isinstance(local["interfaces"], list) or not local["interfaces"]:
            raise ValueError(f"component {item['id']} local manifest needs at least one interface")
        if not isinstance(local["external_dependencies"], list) or not local["external_dependencies"]:
            raise ValueError(f"component {item['id']} local manifest needs external dependencies")
        if not isinstance(local["commands"], dict) or not local["commands"]:
            raise ValueError(f"component {item['id']} local manifest needs maintenance commands")
    integration_paths = document.get("integration_paths")
    if not isinstance(integration_paths, list) or any(not isinstance(path, str) for path in integration_paths):
        raise ValueError("integration_paths must be a list of strings")
    return document


def _matches(path: str, pattern: str) -> bool:
    path = path.removeprefix("./")
    if fnmatch.fnmatchcase(path, pattern):
        return True
    if pattern.endswith("/**"):
        return path == pattern[:-3].rstrip("/")
    return False


def classify_paths(paths: list[str], manifest: dict) -> dict[str, list[str]]:
    components = {item["id"]: [] for item in manifest["components"]}
    components["integration"] = []
    component_patterns = {
        item["id"]: item["paths"] for item in manifest["components"]
    }
    integration_patterns = manifest["integration_paths"]
    for raw_path in paths:
        path = raw_path.strip().replace("\\", "/")
        if not path:
            continue
        owners = [
            component_id
            for component_id, patterns in component_patterns.items()
            if any(_matches(path, pattern) for pattern in patterns)
        ]
        in_integration = any(_matches(path, pattern) for pattern in integration_patterns)
        if len(owners) == 1 and not in_integration:
            components[owners[0]].append(path)
        else:
            components["integration"].append(path)
    return {key: sorted(value) for key, value in components.items()}


def _python_imports(path: Path) -> list[str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError):
        return []
    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            # Relative imports resolve inside the current component package.
            # Filesystem-level cross-component relative imports are checked by
            # the path scanner below, where the referenced directory is known.
            imports.append(node.module)
    return imports


def boundary_violations() -> list[str]:
    violations: list[str] = []
    forbidden_python = {
        "quant-research": (ROOT / "quant-service", ("feishu", "xhs")),
        "feishu-relay": (ROOT / "feishu-relay", ("quant_service", "xhs_intel")),
        "xhs-intel": (ROOT / "xhs-intel", ("app", "feishu")),
    }
    for component_id, (root, prefixes) in forbidden_python.items():
        for path in sorted(root.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            for imported in _python_imports(path):
                if imported == "app" and component_id == "xhs-intel":
                    violations.append(f"{path.relative_to(ROOT)} imports quant package {imported}")
                elif imported.startswith(prefixes):
                    violations.append(f"{path.relative_to(ROOT)} imports outside {component_id}: {imported}")

    cross_path = re.compile(r"(?:quant-service|feishu-relay|xhs-intel)/")
    for root, component_id in ((ROOT / "quant-service", "quant-research"),
                               (ROOT / "feishu-relay", "feishu-relay"),
                               (ROOT / "xhs-intel", "xhs-intel")):
        for suffix in ("*.mjs", "*.py"):
            for path in sorted(root.rglob(suffix)):
                if "__pycache__" in path.parts:
                    continue
                text = path.read_text(encoding="utf-8", errors="replace")
                for match in cross_path.finditer(text):
                    referenced = match.group(0).split("/", 1)[0]
                    if ((component_id == "quant-research" and referenced != "quant-service")
                            or (component_id == "feishu-relay" and referenced != "feishu-relay")
                            or (component_id == "xhs-intel" and referenced != "xhs-intel")):
                        # Ignore documentation strings and runtime URLs; only flag
                        # relative filesystem imports or package paths.
                        line = text.count("\n", 0, match.start()) + 1
                        before = text[max(0, match.start() - 3):match.start()]
                        if before in {"./", "../"}:
                            violations.append(f"{path.relative_to(ROOT)}:{line} crosses into {referenced}")
    return sorted(set(violations))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("paths", nargs="*", help="changed repository paths to classify")
    parser.add_argument("--check", action="store_true", help="validate manifest and import boundaries")
    args = parser.parse_args()
    try:
        manifest = load_manifest()
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"component manifest invalid: {error}", file=sys.stderr)
        return 1

    if args.check:
        violations = boundary_violations()
        if violations:
            print("component boundary check failed:", *violations, sep="\n- ")
            return 1
        print("component boundary check passed")

    if args.paths:
        classified = classify_paths(args.paths, manifest)
        impacted = [key for key, values in classified.items() if values]
        print("impacted components: " + (", ".join(impacted) if impacted else "none"))
        for component_id in impacted:
            print(f"[{component_id}]")
            print("\n".join(f"- {path}" for path in classified[component_id]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
