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
    _validate_foreign_contracts(document)
    return document


def _matches(path: str, pattern: str) -> bool:
    path = path.removeprefix("./")
    if fnmatch.fnmatchcase(path, pattern):
        return True
    if pattern.endswith("/**"):
        return path == pattern[:-3].rstrip("/")
    return False


#: 只有出现在 SQL 关键字后面的 schema 限定名才算真的跨库引用。少了这个限制，
#: ``source: 'quant.market.events'`` 这种 HTTP 出处标签会被误判成跨库查表
#: （feishu-relay 的 baidu-pan 归档脚本里就有一堆，它其实走的是 /api/v1 契约）。
SQL_OBJECT = re.compile(
    r"\b(?:FROM|JOIN|INTO|UPDATE|REFERENCES|TABLE)\s+((?:public|quant)\.[a-z_][a-z0-9_]*)",
    re.IGNORECASE,
)

#: 组件各自拥有的 schema：引用自己的不算跨界。``public`` 不属于任何组件
#: （n8n 自己的表和飞书投递台账都在里面），所以任何对它的引用都要申报。
OWNED_SCHEMAS = {"quant-research": ("quant",)}

#: SQL 行注释。扫描前先去掉。
SQL_COMMENT = re.compile(r"--[^\n]*")

COMPONENT_ROOTS = {
    "quant-research": "quant-service",
    "feishu-relay": "feishu-relay",
    "xhs-intel": "xhs-intel",
}


def _validate_foreign_contracts(document: dict) -> None:
    contracts = document.get("foreign_data_contracts")
    if contracts is None:
        return
    if not isinstance(contracts, list):
        raise ValueError("foreign_data_contracts must be a list")
    for entry in contracts:
        for field in ("consumer", "object", "owner", "kind", "note"):
            if not isinstance(entry.get(field), str) or not entry[field]:
                raise ValueError(f"foreign data contract needs a non-empty {field}")
        sites = entry.get("sites")
        if not isinstance(sites, list) or not sites or any(not isinstance(s, str) for s in sites):
            raise ValueError(f"foreign data contract {entry['object']} needs non-empty string sites")


def foreign_data_references(root: Path, component_id: str) -> dict[str, set[str]]:
    """这个组件实际引用到的、不属于自己的 schema 对象：``对象 → {文件}``。"""
    owned = OWNED_SCHEMAS.get(component_id, ())
    found: dict[str, set[str]] = {}
    for suffix in ("*.py", "*.mjs", "*.js"):
        for path in sorted(root.rglob(suffix)):
            if "__pycache__" in path.parts or "node_modules" in path.parts:
                continue
            # 迁移是历史记录，不是每天在跑的耦合：一条**拆掉**跨界外键的迁移也
            # 必须在 downgrade 里写出那个对象名。把它们算进来，白名单就只能越
            # 积越多，棘轮也就失效了。活的耦合看应用代码。
            if "versions" in path.parts and "migrations" in path.parts:
                continue
            # 先去掉 SQL 注释再匹配：注释掉的查询不是活的耦合，而且一句
            # "原来这里是 REFERENCES public.ingestion_jobs" 的说明文字不该让
            # 已经拆掉的耦合看起来还在（这个坑我自己踩过一次）。
            text = SQL_COMMENT.sub(" ", path.read_text(encoding="utf-8", errors="replace"))
            for match in SQL_OBJECT.finditer(text):
                obj = match.group(1).lower()
                if obj.split(".", 1)[0] in owned:
                    continue
                found.setdefault(obj, set()).add(path.relative_to(ROOT).as_posix())
    return found


def foreign_data_violations(document: dict) -> list[str]:
    """跨界数据引用必须逐条申报，而且只能出现在申报的那个文件里。

    自带的 import/路径检查看不见 SQL：跨 schema 外键和读第三方表都是字符串，
    所以"职责分离"过去只能靠人记得。这里把它变成会红的检查 —— 既挡新增的
    跨界引用，也挡把已申报的对象抄到第二个文件里（n8n 那三张表就被抄过一次，
    其中一份还在 routers/ 里，而 AGENTS.md 规定 routers 只做 HTTP 边界）。
    """
    declared: dict[tuple[str, str], set[str]] = {}
    notes: dict[tuple[str, str], str] = {}
    for entry in document.get("foreign_data_contracts") or []:
        key = (entry["consumer"], entry["object"].lower())
        declared[key] = set(entry["sites"])
        notes[key] = entry["owner"]
    violations: list[str] = []
    seen: set[tuple[str, str]] = set()
    for component_id, relative_root in COMPONENT_ROOTS.items():
        for obj, files in sorted(foreign_data_references(ROOT / relative_root, component_id).items()):
            key = (component_id, obj)
            if key not in declared:
                violations.append(
                    f"{component_id} references {obj} in {sorted(files)} without a "
                    "foreign_data_contracts entry in config/components.json")
                continue
            seen.add(key)
            extra = sorted(files - declared[key])
            if extra:
                violations.append(
                    f"{component_id} references {obj} (owned by {notes[key]}) from undeclared "
                    f"site(s) {extra}; keep it in {sorted(declared[key])} or update the contract")
    for key, sites in sorted(declared.items()):
        if key not in seen:
            violations.append(
                f"stale foreign_data_contracts entry: {key[0]} no longer references {key[1]} "
                f"(declared sites {sorted(sites)}); drop the entry so the boundary keeps tightening")
    return violations


def _compose_service_databases(path: Path) -> dict[tuple[str, str], list[str]]:
    """``(主机, 库) → [服务名]``，只看声明了 PG 连接的服务。"""
    import yaml  # noqa: PLC0415 - 只有这一处需要，别让导入成为脚本的硬依赖

    document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    grouped: dict[tuple[str, str], list[str]] = {}
    for name, service in (document.get("services") or {}).items():
        environment = service.get("environment") or {}
        if isinstance(environment, list):
            environment = dict(item.split("=", 1) for item in environment if "=" in item)
        database = environment.get("PGDATABASE") or environment.get("DB_POSTGRESDB_DATABASE")
        host = environment.get("PGHOST") or environment.get("DB_POSTGRESDB_HOST")
        if database and host:
            grouped.setdefault((str(host), str(database)), []).append(str(name))
    return grouped


def shared_database_violations(document: dict) -> list[str]:
    """两个组件的服务落在同一个库里，必须在清单里申报。

    这是"职责分离"里 import 检查完全看不到的那一半：拓扑。47owner 主栈把
    feishu-adapter 和 quant-research* 放进同一个 ``n8n`` 库，而清单只把
    feishu-relay 记作 ``runtime: 47edge`` —— 事实与声明不一致，谁来看都会以为
    两边已经分库了。shared-peer 那套栈已经拆成两个库，是想要的终态。

    申报不是豁免，是让这件事看得见、并且新增一处会红。
    """
    owners = {service: component["id"]
              for component in document.get("components") or []
              for service in component.get("services") or []}
    declared = {(entry["compose"], entry["database"]): set(entry["components"])
                for entry in document.get("shared_runtime_databases") or []}
    violations: list[str] = []
    seen: set[tuple[str, str]] = set()
    for relative in ("compose.yaml", "deploy/compose.server.yaml", "deploy/shared-peer/compose.yaml"):
        path = ROOT / relative
        if not path.is_file():
            continue
        for (_host, database), services in sorted(_compose_service_databases(path).items()):
            components = {owners[name] for name in services if name in owners}
            if len(components) < 2:
                continue
            key = (relative, database)
            seen.add(key)
            if key not in declared:
                violations.append(
                    f"{relative}: database {database} is shared by {sorted(components)} "
                    f"(services {sorted(services)}) without a shared_runtime_databases entry")
            elif declared[key] != components:
                violations.append(
                    f"{relative}: database {database} is shared by {sorted(components)} but the "
                    f"manifest declares {sorted(declared[key])}")
    for key in sorted(set(declared) - seen):
        violations.append(
            f"stale shared_runtime_databases entry: {key[0]} no longer shares {key[1]} "
            "between components; drop it so the boundary keeps tightening")
    return violations


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
        violations = (boundary_violations() + foreign_data_violations(manifest)
                      + shared_database_violations(manifest))
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
