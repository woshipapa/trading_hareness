"""Static completeness inventory for source adapters (TDX integration plan, P0).

Every public *reader* under ``sources/`` (sub-packages included) must be bound to a capability or listed
in ``UNREGISTERED`` with a non-empty reason.

A reader is found by behaviour, not by name: a public module-level function, or a public method of a
public class, that is ``async`` or reaches input/output on its call path. Input/output is a call into
socket, ssl, urllib.request, http.client, httpx, requests, aiohttp, subprocess or ftplib, asyncio's
connection and subprocess helpers, the builtin ``open``, a file read/write/listing (``read_text``,
``read_bytes``, ``write_text``, ``write_bytes``, ``open``, ``iterdir``, ``glob``, ``rglob``) or a socket
``send``/``sendall``/``recv``/``recv_into``. The call path follows calls inside the module, through
``self``, into a class of the module (constructing it counts as using all of its methods), into other
``sources`` modules and into the package helpers such as ``..http``, and functions passed as arguments
(``asyncio.to_thread(call_sync, ...)``).

*Bound* means reachable from the fetcher argument of a ``resolver.bind(source, capability, fetcher)``
call in ``bindings.py``: through lambdas, helper functions defined in that file, and the for-loop over a
module registry such as ``news_flash.FETCHERS``. A reference anywhere else in that file does not count.
A catalog ``adapter`` string ``app/datasources/sources/<module>.py:<function>`` also counts, and must
name a function that exists.

A TDX command family counts as covered only when a TDX source binds a capability of that family;
otherwise it must be listed in ``UNREGISTERED``. The exemptions are checked too: an empty reason, a name
that is not a reader or family, and a reader or family that is bound now are all problems.

The check parses the files and imports none of them, so it runs without credentials or network.
"""

from __future__ import annotations

import ast
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
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
    "tdx_local_files.write_csv": "offline export helper: writes the parsed rows to CSV; it reads nothing upstream",
    "fuyao_evidence.fetch_code_batches": "batching helper of the post-close valuation archive "
                                         "(collectors/post_close.py) and the auction capture (market_event_capture.py); "
                                         "no capability of its own",
    "tencent_limits.session_limit_cross_section": "composition-root path: main.py stores the session's limit prices "
                                                  "(daily_trade_limits) before the first intraday scan; not resolver-routed",
    "tdx_protocol.TdxClient.quotes": "legacy quote command 0x053e; bound in P2 after the instrument model (delta D2)",
    "tdx_protocol.TdxClient.bars": "legacy bar command 0x052d; bound in P2 after the instrument model (delta D2)",
    "tdx_protocol.TdxClient.ticks": "transport for ticks.fetch_tdx_ticks, which is the bound reader",
    "tdx_protocol.TdxClient.xdxr": "transport for ticks.fetch_tdx_capital_changes, which is the bound reader",
    "tdx_protocol.sweep_sync": "per-section transport over one deterministic host (delta D1/D5); the instrument "
                               "and security-list capabilities of P2 bind its sections",
    "tdx_instruments.TdxInstrumentClient.security_count": "harvested, binds in I1b/I2/I3/I4",
    "tdx_instruments.TdxInstrumentClient.security_list": "harvested, binds in I1b/I2/I3/I4",
    "tdx_instruments.TdxInstrumentClient.index_bars": "harvested, binds in I1b/I2/I3/I4",
    "tdx_files.TdxFilesClient.file_size": "harvested, binds in I1b/I2/I3/I4",
    "tdx_files.TdxFilesClient.download": "harvested, binds in I1b/I2/I3/I4",
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


#: External modules (dotted prefixes) whose calls are input/output.
IO_MODULES = ("socket", "ssl", "urllib.request", "http.client", "httpx", "requests", "aiohttp", "subprocess", "ftplib",
              "asyncio.open_connection", "asyncio.create_subprocess_exec", "asyncio.create_subprocess_shell")
#: Method names that are input/output whatever object they are called on.
IO_ATTRIBUTES = frozenset({"read_text", "read_bytes", "write_text", "write_bytes", "open", "iterdir", "glob", "rglob",
                           "send", "sendall", "recv", "recv_into", "urlopen"})
_DEFS = (ast.FunctionDef, ast.AsyncFunctionDef)


@dataclass
class _Module:
    name: str                                   # dotted, relative to the package root ("sources.ticks", "http")
    tree: ast.Module
    functions: dict[str, ast.AST] = field(default_factory=dict)    # "f" and "Class.method"
    classes: dict[str, list[str]] = field(default_factory=dict)    # class -> method names
    modules: dict[str, str] = field(default_factory=dict)          # local alias -> internal module
    names: dict[str, str] = field(default_factory=dict)            # local alias -> internal "module.name"
    external: dict[str, str] = field(default_factory=dict)         # local alias -> external dotted name


def _module_name(path: Path, package_root: Path) -> str:
    return ".".join(path.relative_to(package_root).with_suffix("").parts)


def _resolve_relative(current: str, level: int, module: str | None) -> str:
    parts = current.split(".")[:-1]                 # the package that holds ``current``
    if level > 1:
        parts = parts[:len(parts) - (level - 1)]
    return ".".join([*parts, *(module.split(".") if module else [])])


def _load(package_root: Path) -> dict[str, _Module]:
    modules: dict[str, _Module] = {}
    for path in sorted(package_root.rglob("*.py")):
        name = _module_name(path, package_root)
        info = _Module(name, ast.parse(path.read_text(encoding="utf-8"), filename=str(path)))
        for node in info.tree.body:
            if isinstance(node, _DEFS):
                info.functions[node.name] = node
            elif isinstance(node, ast.ClassDef):
                methods = [item.name for item in node.body if isinstance(item, _DEFS)]
                info.classes[node.name] = methods
                for item in node.body:
                    if isinstance(item, _DEFS):
                        info.functions[f"{node.name}.{item.name}"] = item
        modules[name] = info
    for info in modules.values():
        for node in ast.walk(info.tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    info.external[alias.asname or alias.name.split(".")[0]] = alias.name if alias.asname else alias.name.split(".")[0]
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    base = _resolve_relative(info.name, node.level, node.module)
                    for alias in node.names:
                        local = alias.asname or alias.name
                        if f"{base}.{alias.name}".strip(".") in modules:
                            info.modules[local] = f"{base}.{alias.name}".strip(".")
                        elif base in modules:
                            info.names[local] = f"{base}.{alias.name}"
                elif node.module:
                    for alias in node.names:
                        info.external[alias.asname or alias.name] = f"{node.module}.{alias.name}"
    return modules


def _is_io_name(dotted: str) -> bool:
    return any(dotted == prefix or dotted.startswith(prefix + ".") for prefix in IO_MODULES)


def _attribute_chain(node: ast.AST) -> tuple[str, list[str]] | None:
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        return node.id, list(reversed(parts))
    return None


def _edges(modules: dict[str, _Module], info: _Module, owner: str | None, node: ast.AST) -> tuple[bool, set[str]]:
    """``(does direct input/output, callees as "module:function")`` of one function body."""
    io, callees = False, set()

    def target_of(expr: ast.AST) -> set[str]:
        found: set[str] = set()
        if isinstance(expr, ast.Name):
            name = expr.id
            if name in info.functions:
                found.add(f"{info.name}:{name}")
            if name in info.classes:
                found |= {f"{info.name}:{name}.{method}" for method in info.classes[name]}
            if name in info.names:
                module, _, attr = info.names[name].rpartition(".")
                target = modules.get(module)
                if target is not None:
                    found |= {f"{module}:{key}" for key in target.functions if key == attr or key.startswith(attr + ".")}
        chain = _attribute_chain(expr) if isinstance(expr, ast.Attribute) else None
        if chain is not None:
            root, attrs = chain
            if root in {"self", "cls"} and owner is not None and attrs:
                found.add(f"{info.name}:{owner}.{attrs[0]}")
            elif root in info.modules and attrs:
                target = modules[info.modules[root]]
                found |= {f"{target.name}:{key}" for key in target.functions if key == attrs[0] or key.startswith(attrs[0] + ".")}
        return found

    for sub in ast.walk(node):
        if isinstance(sub, ast.Call):
            func = sub.func
            if isinstance(func, ast.Name):
                if func.id == "open" and func.id not in info.functions and func.id not in info.names:
                    io = True
                elif func.id in info.external and _is_io_name(info.external[func.id]):
                    io = True
            elif isinstance(func, ast.Attribute):
                if func.attr in IO_ATTRIBUTES:
                    io = True
                chain = _attribute_chain(func)
                if chain is not None and chain[0] in info.external:
                    if _is_io_name(".".join([info.external[chain[0]], *chain[1]])):
                        io = True
            callees |= target_of(func)
            for argument in [*sub.args, *(keyword.value for keyword in sub.keywords)]:
                if isinstance(argument, (ast.Name, ast.Attribute)):
                    callees |= target_of(argument)
    return io, callees


def _readers(package_root: Path, source_package: str) -> tuple[list[str], dict[str, _Module]]:
    modules = _load(package_root)
    io: dict[str, bool] = {}
    graph: dict[str, set[str]] = {}
    for info in modules.values():
        for key, node in info.functions.items():
            owner = key.split(".", 1)[0] if "." in key else None
            direct, callees = _edges(modules, info, owner, node)
            io[f"{info.name}:{key}"] = direct or isinstance(node, ast.AsyncFunctionDef)
            graph[f"{info.name}:{key}"] = callees
    changed = True
    while changed:
        changed = False
        for key, callees in graph.items():
            if not io[key] and any(io.get(callee) for callee in callees):
                io[key] = changed = True
    readers = []
    prefix = source_package + "."
    for info in modules.values():
        if not info.name.startswith(prefix):
            continue
        public_classes = {name for name in info.classes if not name.startswith("_")}
        for key in info.functions:
            head, _, method = key.partition(".")
            if head.startswith("_") or (method and (method.startswith("_") or head not in public_classes)):
                continue
            if io[f"{info.name}:{key}"]:
                readers.append(f"{info.name[len(prefix):]}.{key}")
    return sorted(readers), modules


def public_fetch_functions(root: Path = SOURCE_ROOT) -> tuple[str, ...]:
    """Readers under ``root`` as ``module.function`` / ``module.Class.method`` (sub-packages dotted)."""
    return tuple(_readers(root.parent, root.name)[0])


def _registry_members(root: Path, module: str, name: str) -> set[str]:
    """Functions of ``module`` named inside its module-level registry ``name`` (e.g. ``FETCHERS``)."""
    path = root / (module.replace(".", "/") + ".py")
    if not path.exists():
        return set()
    tree = ast.parse(path.read_text(encoding="utf-8"))
    functions = {node.name for node in tree.body if isinstance(node, _DEFS)}
    for node in tree.body:
        targets = node.targets if isinstance(node, ast.Assign) else [node.target] if isinstance(node, ast.AnnAssign) else []
        if any(isinstance(target, ast.Name) and target.id == name for target in targets) and node.value is not None:
            return {f"{module}.{inner.id}" for inner in ast.walk(node.value)
                    if isinstance(inner, ast.Name) and inner.id in functions}
    return set()


def referenced_by_bindings(bindings_module: Path = BINDINGS_MODULE, root: Path = SOURCE_ROOT) -> set[str]:
    """Readers reachable from the fetcher argument of a ``.bind(source, capability, fetcher)`` call."""
    tree = ast.parse(bindings_module.read_text(encoding="utf-8"))
    package = root.name
    modules: set[str] = set()
    names: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level == 1 and node.module:
            if node.module == package:
                modules.update(alias.asname or alias.name for alias in node.names)
            elif node.module.startswith(package + "."):
                module = node.module.split(".", 1)[1]
                names.update({alias.asname or alias.name: f"{module}.{alias.name}" for alias in node.names})
    defs: dict[str, list[ast.AST]] = {}
    loops: dict[str, list[ast.AST]] = {}
    for node in ast.walk(tree):
        if isinstance(node, _DEFS):
            defs.setdefault(node.name, []).append(node)
        elif isinstance(node, (ast.For, ast.AsyncFor)):
            for target in ast.walk(node.target):
                if isinstance(target, ast.Name):
                    loops.setdefault(target.id, []).append(node.iter)
    found: set[str] = set()
    seen: set[int] = set()

    def visit(start: ast.AST) -> None:
        for sub in ast.walk(start):
            if isinstance(sub, ast.Attribute) and isinstance(sub.value, ast.Name) and sub.value.id in modules:
                found.add(f"{sub.value.id}.{sub.attr}")
                if sub.attr.isupper():
                    found.update(_registry_members(root, sub.value.id, sub.attr))
            elif isinstance(sub, ast.Name):
                if sub.id in names:
                    found.add(names[sub.id])
                for nested in [*defs.get(sub.id, ()), *loops.get(sub.id, ())]:
                    if id(nested) not in seen:
                        seen.add(id(nested))
                        visit(nested)

    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "bind":
            fetcher = node.args[2] if len(node.args) >= 3 else next(
                (keyword.value for keyword in node.keywords if keyword.arg == "fetcher"), None)
            if fetcher is not None:
                visit(fetcher)
    return found


def referenced_by_catalog(adapters: Iterable[str | None], root: Path = SOURCE_ROOT) -> tuple[set[str], list[str]]:
    """``(module.function names, adapters naming nothing)`` for adapter strings that point into ``root``.

    The part after ``:`` must be a function or ``Class.method`` of the module, or a string literal in it
    (data-driven adapters such as ``eastmoney_datacenter.py:RPT_LIFT_STAGE`` name a report id). Only a
    function counts as a bound reader.
    """
    found, missing = set(), []
    for adapter in adapters:
        if not adapter or not adapter.startswith(SOURCES_PREFIX) or ".py:" not in adapter:
            continue
        path, function = adapter[len(SOURCES_PREFIX):].split(".py:", 1)
        function = function.strip()
        module_path = root / f"{path}.py"
        names: set[str] = set()
        literals: set[str] = set()
        if module_path.exists():
            tree = ast.parse(module_path.read_text(encoding="utf-8"))
            for node in tree.body:
                if isinstance(node, _DEFS):
                    names.add(node.name)
                elif isinstance(node, ast.ClassDef):
                    names |= {f"{node.name}.{item.name}" for item in node.body if isinstance(item, _DEFS)}
            literals = {node.value for node in ast.walk(tree) if isinstance(node, ast.Constant) and isinstance(node.value, str)}
        if function in names:
            found.add(f"{path.replace('/', '.')}.{function}")
        elif function not in literals:      # data-driven adapters name a report id, e.g. eastmoney RPT_LIFT_STAGE
            missing.append(adapter)
    return found, missing


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
    from_catalog, missing = referenced_by_catalog((getattr(item, "adapter", None) for item in bindings), root)
    bound = referenced_by_bindings(bindings_module, root) | from_catalog
    problems = [f"catalog adapter {adapter} names no function in that module" for adapter in missing]
    problems += [f"unregistered source fetch function: {name}"
                 for name in readers if name not in bound and name not in unregistered]
    tdx = [(getattr(item, "source", ""), getattr(item, "capability", "")) for item in bindings
           if getattr(item, "status", "") != "retired"]
    covered = set()
    for family, (prefixes, sources) in TDX_COMMAND_FAMILIES.items():
        if any(source in sources and any(capability.startswith(prefix) for prefix in prefixes) for source, capability in tdx):
            covered.add(family)
        elif f"family:{family}" not in unregistered:
            problems.append(f"unaccounted TDX command family: {family}")
    for name, reason in sorted(unregistered.items()):
        if not str(reason or "").strip():
            problems.append(f"UNREGISTERED entry {name} has no reason")
        if name.startswith("family:"):
            family = name.split(":", 1)[1]
            if family not in TDX_COMMAND_FAMILIES:
                problems.append(f"stale UNREGISTERED entry: {name} (no such TDX command family)")
            elif family in covered:
                problems.append(f"stale UNREGISTERED entry: {name} (a TDX binding covers it now; delete the entry)")
        elif name not in readers:
            problems.append(f"stale UNREGISTERED entry: {name} (not a reader under sources/)")
        elif name in bound:
            problems.append(f"stale UNREGISTERED entry: {name} (bound now; delete the entry)")
    return problems


__all__ = ["IO_ATTRIBUTES", "IO_MODULES", "TDX_COMMAND_FAMILIES", "UNREGISTERED", "completeness_problems",
           "public_fetch_functions", "referenced_by_bindings", "referenced_by_catalog"]
