#!/usr/bin/env python3
"""Read the Alembic lineage from migration files, without a database or Alembic.

    migration_lineage.py <versions-dir> --head              print the single head
    migration_lineage.py <versions-dir> --check <db-rev>    exit 0 at head, 3 behind, 4 unknown, 2 if broken
    migration_lineage.py <versions-dir> --pending <db-rev>  the revisions <db-rev> lacks, oldest first

The owner applies owner-schema migrations itself (RELEASE_SYNC_47 stage D); a
release must not switch code onto a database that has not caught up. The
running service reports its database revision in /health, so a release can
compare the two before it changes anything. Stdlib only, so it runs on the
owner host straight out of the release archive.
"""

from __future__ import annotations

import argparse
import ast
from pathlib import Path
import sys


def _assigned(tree: ast.Module, name: str) -> object:
    for node in tree.body:
        targets: list[ast.expr] = []
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets, value = [node.target], node.value
        else:
            continue
        if any(isinstance(target, ast.Name) and target.id == name for target in targets):
            return ast.literal_eval(value)
    raise KeyError(name)


def read_lineage(versions: Path) -> dict[str, tuple[str, ...]]:
    """``revision -> (down_revision, ...)`` for every migration file."""
    lineage: dict[str, tuple[str, ...]] = {}
    for path in sorted(versions.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        revision = _assigned(tree, "revision")
        down = _assigned(tree, "down_revision")
        parents = tuple(down) if isinstance(down, (tuple, list)) else (() if down is None else (down,))
        if revision in lineage:
            raise ValueError(f"revision {revision} is defined twice")
        lineage[str(revision)] = tuple(str(parent) for parent in parents)
    return lineage


def heads(lineage: dict[str, tuple[str, ...]]) -> list[str]:
    referenced = {parent for parents in lineage.values() for parent in parents}
    return sorted(revision for revision in lineage if revision not in referenced)


def ancestors(lineage: dict[str, tuple[str, ...]], revision: str) -> set[str]:
    seen: set[str] = set()
    stack = [revision]
    while stack:
        for parent in lineage.get(stack.pop(), ()):
            if parent not in seen:
                seen.add(parent)
                stack.append(parent)
    return seen


def schema_status(lineage: dict[str, tuple[str, ...]], database_revision: str) -> str:
    """``at_head``, ``behind`` (the code needs migrations the database lacks) or ``unknown``."""
    found = heads(lineage)
    if len(found) != 1:
        raise ValueError(f"expected one migration head, found {found}")
    head = found[0]
    if database_revision == head:
        return "at_head"
    if database_revision in ancestors(lineage, head):
        return "behind"
    return "unknown"


def pending(lineage: dict[str, tuple[str, ...]], database_revision: str) -> list[str]:
    """The revisions between ``database_revision`` and the head, parents before children."""
    found = heads(lineage)
    if len(found) != 1:
        raise ValueError(f"expected one migration head, found {found}")
    applied = ancestors(lineage, database_revision) | {database_revision}
    needed = (ancestors(lineage, found[0]) | {found[0]}) - applied
    ordered: list[str] = []
    while needed:
        ready = sorted(revision for revision in needed
                       if all(parent not in needed for parent in lineage[revision]))
        ordered.extend(ready)
        needed -= set(ready)
    return ordered


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("versions", type=Path)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--head", action="store_true")
    group.add_argument("--check", metavar="DB_REVISION")
    group.add_argument("--pending", metavar="DB_REVISION")
    args = parser.parse_args()
    try:
        lineage = read_lineage(args.versions)
        if args.head:
            found = heads(lineage)
            if len(found) != 1:
                raise ValueError(f"expected one migration head, found {found}")
            print(found[0])
            return 0
        if args.pending:
            print("\n".join(pending(lineage, args.pending)))
            return 0
        status = schema_status(lineage, args.check)
    except (ValueError, KeyError, SyntaxError, OSError) as error:
        print(f"migration lineage unreadable: {error}", file=sys.stderr)
        return 2
    print(status)
    return {"at_head": 0, "behind": 3, "unknown": 4}[status]


if __name__ == "__main__":
    raise SystemExit(main())
