"""Static completeness inventory for source adapters (TDX integration plan, P0).

Every public reader in ``sources/*`` must be bound to a capability or listed in
``UNREGISTERED`` with a reason. "Bound" is derived from the code, never from a
hand-kept list:

- the functions ``bindings.py`` actually references: ``module.function`` after
  ``from .sources import module``, names imported from ``.sources.module``, and
  the functions listed in a module-level registry such as ``news_flash.FETCHERS``
  when ``bindings.py`` references that registry;
- the catalog's ``adapter`` strings that point into ``app/datasources/sources``.

Public readers are module-level functions that are ``async`` or named
``fetch_*``, and every public method of a class whose name ends in ``Client``.
The check parses the files and imports none of them, so it runs without
credentials or network.

A TDX command family counts as covered only when a TDX source binds a capability
of that family; otherwise the family must be listed in ``UNREGISTERED`` with a
reason. An ``UNREGISTERED`` entry that no longer names anything is itself a problem,
so the list cannot keep stale exemptions.
"""

from __future__ import annotations

import ast
from collections.abc import Iterable, Mapping
from pathlib import Path

PACKAGE_ROOT = Path(__file__).parent
SOURCE_ROOT = PACKAGE_ROOT / "sources"
BINDINGS_MODULE = PACKAGE_ROOT / "bindings.py"
SOURCES_PREFIX = "app/datasources/sources/"

#: Public readers that are deliberately not capability bindings yet, with the reason.
UNREGISTERED: dict[str, str] = {
    "tdx_protocol.call": "transport primitive; ticks.fetch_tdx_ticks is the bound reader",
    "tdx_protocol.call_sync": "transport primitive; ticks.fetch_tdx_ticks is the bound reader",
    "tdx_local_files.read_file": "offline import helper; the byte parsers are bound by their callers",
    "tdx_local_files.discover": "offline file discovery helper; no row contract of its own",
    "fuyao_evidence.fetch_code_batches": "batching helper of the post-close valuation archive "
                                         "(collectors/post_close.py) and the auction capture (market_event_capture.py); "
                                         "no capability of its own",
    "tencent_limits.session_limit_cross_section": "composition-root path: main.py stores the session's limit prices "
                                                  "(daily_trade_limits) before the first intraday scan; not resolver-routed",
    "tdx_protocol.TdxClient.quotes": "legacy quote command 0x053e; bound in P2 after the instrument model (delta D2)",
    "tdx_protocol.TdxClient.bars": "legacy bar command 0x052d; bound in P2 after the instrument model (delta D2)",
    "tdx_protocol.TdxClient.ticks": "transport for ticks.fetch_tdx_ticks, which is the bound reader",
    "tdx_protocol.TdxClient.xdxr": "transport for ticks.fetch_tdx_capital_changes, which is the bound reader",
    "family:quote": "legacy quotes bind in P2 after the instrument model (delta D2); MAC 0x122b in P3",
    "family:F10": "verified in the plan; company-profile capability arrives in P5",
    "family:finance": "verified in the plan; financial-statements capability arrives in P5",
    "family:files": "verified in the plan; server-file capabilities arrive in P4",
    "family:boards": "verified in the plan; board capabilities arrive in P3",
    "family:capital flow": "MAC capital flow semantics unverified (delta 1b); research projection only, P3",
    "family:MAC": "MAC protocol bindings arrive in P3, all UNSUPPORTED until the evidence gates",
    "family:extended": "7727 extended-market handshake is unsolved (plan F7, P9)",
}

#: family -> (capability prefixes, source keys that count as TDX for it)
TDX_SOURCES = ("tdx_public", "tdx_local")
TDX_COMMAND_FAMILIES: Mapping[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "quote": (("quote.",), TDX_SOURCES),
    "bars": (("bars.daily",), TDX_SOURCES),
    "minute": (("bars.minute",), TDX_SOURCES),
    "ticks": (("ticks.",), TDX_SOURCES),
    "auction": (("auction.",), TDX_SOURCES),
    "F10": (("fundamentals.company_profile",), TDX_SOURCES),
    "finance": (("fundamentals.financial_statements",), TDX_SOURCES),
    "files": (("sector.board_catalog", "fundamentals.daily_basic"), TDX_SOURCES),
    "boards": (("sector.",), TDX_SOURCES + ("tdx_mac",)),
    "capital flow": (("flow.",), TDX_SOURCES + ("tdx_mac",)),
    "MAC": (("",), ("tdx_mac",)),
    "extended": (("",), ("tdx_ext",)),
}


def _is_reader(node: ast.AST) -> bool:
    return isinstance(node, ast.AsyncFunctionDef) or (isinstance(node, ast.FunctionDef) and node.name.startswith("fetch_"))


def public_fetch_functions(root: Path = SOURCE_ROOT) -> tuple[str, ...]:
    """``module.function`` and ``module.Class.method`` names of the public readers under ``root``."""
    result: list[str] = []
    for path in sorted(root.glob("*.py")):
        module = path.stem
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not node.name.startswith("_"):
                if _is_reader(node) or node.name in {"call", "call_sync", "read_file", "discover",
                                                     "session_limit_cross_section", "period_report", "suspensions_on"}:
                    result.append(f"{module}.{node.name}")
            elif isinstance(node, ast.ClassDef) and node.name.endswith("Client"):
                for item in node.body:
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and not item.name.startswith("_"):
                        result.append(f"{module}.{node.name}.{item.name}")
    return tuple(result)


def _registry_members(root: Path, module: str, name: str) -> set[str]:
    """Functions of ``module`` named inside its module-level registry ``name`` (e.g. ``FETCHERS``)."""
    path = root / f"{module}.py"
    if not path.exists():
        return set()
    tree = ast.parse(path.read_text(encoding="utf-8"))
    functions = {node.name for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
    for node in tree.body:
        targets = node.targets if isinstance(node, ast.Assign) else [node.target] if isinstance(node, ast.AnnAssign) else []
        if any(isinstance(target, ast.Name) and target.id == name for target in targets) and node.value is not None:
            return {f"{module}.{inner.id}" for inner in ast.walk(node.value)
                    if isinstance(inner, ast.Name) and inner.id in functions}
    return set()


def referenced_by_bindings(bindings_module: Path = BINDINGS_MODULE, root: Path = SOURCE_ROOT) -> set[str]:
    """``module.function`` names that the package binding code actually references."""
    tree = ast.parse(bindings_module.read_text(encoding="utf-8"))
    modules: set[str] = set()
    names: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.level == 1 and node.module:
            if node.module == "sources":
                modules.update(alias.asname or alias.name for alias in node.names)
            elif node.module.startswith("sources."):
                module = node.module.split(".", 1)[1]
                names.update({alias.asname or alias.name: f"{module}.{alias.name}" for alias in node.names})
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id in modules:
            reference = f"{node.value.id}.{node.attr}"
            found.add(reference)
            if node.attr.isupper():
                found |= _registry_members(root, node.value.id, node.attr)
        elif isinstance(node, ast.Name) and node.id in names:
            found.add(names[node.id])
    return found


def referenced_by_catalog(adapters: Iterable[str | None]) -> set[str]:
    """``module.function`` names from catalog adapter strings such as ``app/datasources/sources/ticks.py:fetch_tdx_ticks``."""
    found = set()
    for adapter in adapters:
        if adapter and adapter.startswith(SOURCES_PREFIX) and ".py:" in adapter:
            path, function = adapter[len(SOURCES_PREFIX):].split(".py:", 1)
            found.add(f"{path}.{function.strip()}")
    return found


def completeness_problems(
    bindings: Iterable[object] | None = None,
    *,
    root: Path = SOURCE_ROOT,
    bindings_module: Path = BINDINGS_MODULE,
    unregistered: Mapping[str, str] | None = None,
) -> list[str]:
    if bindings is None:
        from .catalog import BINDINGS
        bindings = BINDINGS
    bindings = list(bindings)
    unregistered = UNREGISTERED if unregistered is None else unregistered
    readers = public_fetch_functions(root)
    bound = referenced_by_bindings(bindings_module, root) | referenced_by_catalog(
        getattr(item, "adapter", None) for item in bindings)
    problems = [f"unregistered source fetch function: {name}"
                for name in readers if name not in bound and name not in unregistered]
    tdx = [(getattr(item, "source", ""), getattr(item, "capability", "")) for item in bindings
           if getattr(item, "status", "") != "retired"]
    for family, (prefixes, sources) in TDX_COMMAND_FAMILIES.items():
        covered = any(source in sources and any(capability.startswith(prefix) for prefix in prefixes)
                      for source, capability in tdx)
        if not covered and f"family:{family}" not in unregistered:
            problems.append(f"unaccounted TDX command family: {family}")
    known = set(readers) | {f"family:{family}" for family in TDX_COMMAND_FAMILIES}
    problems += [f"stale UNREGISTERED entry: {name}" for name in sorted(set(unregistered) - known)]
    return problems


__all__ = ["TDX_COMMAND_FAMILIES", "UNREGISTERED", "completeness_problems", "public_fetch_functions",
           "referenced_by_bindings", "referenced_by_catalog"]
