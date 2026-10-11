#!/usr/bin/env python3
"""Check that every logical component has an isolated runtime contract."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from verify_component_boundaries import ROOT, load_manifest


def _compose_build_contract(compose: str, expected_dockerfile: str, compose_dir: Path, root: Path) -> bool:
    dockerfile_name = (root / expected_dockerfile).relative_to(compose_dir).as_posix()
    return bool(re.search(r"(?m)^\s*context:\s*\.\s*$", compose)) and bool(
        re.search(rf"(?m)^\s*dockerfile:\s*{re.escape(dockerfile_name)}\s*$", compose)
    )


def validate_component_runtimes(root: Path = ROOT) -> list[str]:
    errors: list[str] = []
    manifest = load_manifest()
    for component in manifest["components"]:
        component_id = component["id"]
        manifest_path = root / str(component.get("manifest", ""))
        if not manifest_path.is_file():
            errors.append(f"{component_id}: local component manifest does not exist")
        else:
            try:
                local_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as error:
                errors.append(f"{component_id}: local component manifest cannot be read: {error}")
                local_manifest = {}
            if local_manifest.get("id") != component_id:
                errors.append(f"{component_id}: local component manifest id mismatch")
            if not local_manifest.get("commands"):
                errors.append(f"{component_id}: local component manifest has no commands")
        runtime = component.get("standalone")
        if not isinstance(runtime, dict):
            errors.append(f"{component_id}: missing standalone runtime contract")
            continue
        for key in ("build_context", "dockerfile", "compose", "entrypoint", "external_contracts"):
            if key not in runtime:
                errors.append(f"{component_id}: standalone.{key} is missing")
        context = root / str(runtime.get("build_context", ""))
        dockerfile = root / str(runtime.get("dockerfile", ""))
        compose_path = root / str(runtime.get("compose", ""))
        entrypoint = root / str(runtime.get("entrypoint", ""))
        for label, path in (("build_context", context), ("dockerfile", dockerfile),
                            ("compose", compose_path), ("entrypoint", entrypoint)):
            if not path.exists():
                errors.append(f"{component_id}: standalone {label} does not exist: {path.relative_to(root)}")
        if context.is_dir() and dockerfile.is_file():
            dockerfile_text = dockerfile.read_text(encoding="utf-8")
            if re.search(r"(?m)^\s*COPY\s+\.\./", dockerfile_text):
                errors.append(f"{component_id}: standalone Dockerfile copies outside its context")
            if "npm ci" in dockerfile_text and not (context / "adapter" / "package-lock.json").is_file():
                errors.append(f"{component_id}: standalone Node image uses npm ci without adapter/package-lock.json")
            if "requirements.lock" in dockerfile_text and not (context / "requirements.lock").is_file():
                errors.append(f"{component_id}: standalone image references a missing requirements.lock")
            if "requirements.lock" in dockerfile_text and component_id == "feishu-relay" and not (context / "bridge" / "requirements.lock").is_file():
                errors.append(f"{component_id}: bridge image references a missing bridge/requirements.lock")
        if compose_path.is_file():
            compose_text = compose_path.read_text(encoding="utf-8")
            if dockerfile.is_file() and not _compose_build_contract(compose_text, str(runtime["dockerfile"]), compose_path.parent, root):
                errors.append(f"{component_id}: standalone compose does not build {dockerfile.relative_to(root)} from its directory")
        contracts = runtime.get("external_contracts")
        if not isinstance(contracts, list) or not contracts or any(not str(item).strip() for item in contracts):
            errors.append(f"{component_id}: standalone external_contracts must be explicit and non-empty")
        for supporting in runtime.get("supporting_runtimes", []):
            if not isinstance(supporting, dict):
                errors.append(f"{component_id}: supporting runtime must be an object")
                continue
            for key in ("id", "dockerfile", "entrypoint", "external_contracts"):
                if key not in supporting:
                    errors.append(f"{component_id}: supporting runtime missing {key}")
            for label, value in (("dockerfile", supporting.get("dockerfile")),
                                 ("entrypoint", supporting.get("entrypoint"))):
                if value and not (root / str(value)).is_file():
                    errors.append(f"{component_id}: supporting {label} does not exist: {value}")
            supporting_dockerfile = root / str(supporting.get("dockerfile", ""))
            if supporting_dockerfile.is_file():
                supporting_text = supporting_dockerfile.read_text(encoding="utf-8")
                if "requirements.lock" in supporting_text and not (supporting_dockerfile.parent / "requirements.lock").is_file():
                    errors.append(f"{component_id}: supporting runtime references a missing local requirements.lock")
            supporting_contracts = supporting.get("external_contracts")
            if not isinstance(supporting_contracts, list) or not supporting_contracts:
                errors.append(f"{component_id}: supporting runtime external_contracts must be non-empty")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="validate all standalone contracts")
    args = parser.parse_args()
    if not args.check:
        parser.error("--check is required")
    errors = validate_component_runtimes()
    if errors:
        print("component runtime contract check failed:", *errors, sep="\n- ")
        return 1
    print("component runtime contract check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
