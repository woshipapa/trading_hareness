"""No transaction may stay open across network I/O or a sleep.

A transaction held while the process waits on something else leaves its
connection ``idle in transaction``: it keeps a pool slot, a snapshot and
possibly locks for as long as the other thing takes.  The peer shares a
database capped at 50 connections, and on 2026-09-23 the guard twice
restarted the API because the pool could not get a connection.

This is a static guard.  It walks every module and fails if

* an ``async with <x>.transaction()`` block awaits anything that is not the
  transaction's own work - a statement, a fetch, a helper handed the
  connection, or a closure that uses it;
* a ``with <x>.transaction()`` block calls the network or sleeps.

A genuine exception belongs in ``ALLOWED`` with the reason next to it.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app"

DB_WORK = {"execute", "executemany", "fetchone", "fetchall", "fetchmany", "cursor", "copy", "commit",
           "rollback", "transaction", "write_row", "read", "set_autocommit", "fetch", "fetchval", "fetchrow"}
SLOW_SYNC = ("requests.", "httpx.", "urlopen", "time.sleep", "subprocess.", "socket.", "asyncio.run",
             "call_provider", "call_api", "send_alert", "_super_get_http_get")
#: (module, line) pairs reviewed and accepted, each with its reason.
ALLOWED: dict[tuple[str, int], str] = {}


def _name(call: ast.Call) -> str:
    parts, func = [], call.func
    while isinstance(func, ast.Attribute):
        parts.append(func.attr)
        func = func.value
    if isinstance(func, ast.Name):
        parts.append(func.id)
    return ".".join(reversed(parts))


def _opens_transaction(node: ast.With | ast.AsyncWith) -> bool:
    return any(isinstance(item.context_expr, ast.Call)
               and _name(item.context_expr).split(".")[-1] == "transaction" for item in node.items)


def _bound(node: ast.With | ast.AsyncWith) -> set[str]:
    return {item.optional_vars.id for item in node.items if isinstance(item.optional_vars, ast.Name)}


def _closures_using(body: list[ast.stmt], bound: set[str]) -> set[str]:
    """Nested functions defined in the block that read the transaction's connection."""
    names = set()
    for statement in ast.walk(ast.Module(body=body, type_ignores=[])):
        if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if any(isinstance(inner, ast.Name) and inner.id in bound for inner in ast.walk(statement)):
                names.add(statement.name)
    return names


def violations() -> list[str]:
    found = []
    for path in sorted(APP.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        module = str(path.relative_to(APP))
        for node in ast.walk(tree):
            if isinstance(node, ast.AsyncWith) and _opens_transaction(node):
                bound = _bound(node)
                closures = _closures_using(node.body, bound)
                for inner in ast.walk(ast.Module(body=node.body, type_ignores=[])):
                    if not isinstance(inner, ast.Await) or not isinstance(inner.value, ast.Call):
                        continue
                    name = _name(inner.value)
                    arguments = list(inner.value.args) + [keyword.value for keyword in inner.value.keywords]
                    if (name.split(".")[-1] in DB_WORK or name in closures
                            or any(isinstance(arg, ast.Name) and arg.id in bound for arg in arguments)):
                        continue
                    if (module, inner.lineno) not in ALLOWED:
                        found.append(f"{module}:{inner.lineno} awaits {name}() inside an open transaction")
            elif isinstance(node, ast.With) and _opens_transaction(node):
                for inner in ast.walk(ast.Module(body=node.body, type_ignores=[])):
                    if isinstance(inner, ast.Call):
                        name = _name(inner)
                        if any(name.startswith(slow) or name.endswith("." + slow.rstrip(".")) for slow in SLOW_SYNC):
                            if (module, inner.lineno) not in ALLOWED:
                                found.append(f"{module}:{inner.lineno} calls {name}() inside an open transaction")
    return found


class TransactionIoTests(unittest.TestCase):
    def test_no_transaction_waits_on_the_network_or_a_sleep(self):
        self.assertEqual(violations(), [])

    def test_the_guard_actually_catches_the_pattern(self):
        # A guard that cannot fail is not a guard: run it on a known-bad sample.
        sample = ast.parse(
            "async def f(db, client):\n"
            "    async with db.transaction() as connection:\n"
            "        await connection.execute('select 1')\n"
            "        await client.get('https://example.invalid')\n")
        node = next(n for n in ast.walk(sample) if isinstance(n, ast.AsyncWith))
        bound = _bound(node)
        awaited = [_name(n.value) for n in ast.walk(ast.Module(body=node.body, type_ignores=[]))
                   if isinstance(n, ast.Await) and isinstance(n.value, ast.Call)]
        flagged = [name for name in awaited if name.split(".")[-1] not in DB_WORK
                   and name not in _closures_using(node.body, bound)]
        self.assertEqual(flagged, ["client.get"])

    def test_a_closure_over_the_connection_is_the_transactions_own_work(self):
        sample = ast.parse(
            "async def f(db):\n"
            "    async with db.transaction() as connection:\n"
            "        async def one(sql):\n"
            "            return await (await connection.execute(sql)).fetchone()\n"
            "        await one('select 1')\n")
        node = next(n for n in ast.walk(sample) if isinstance(n, ast.AsyncWith))
        self.assertIn("one", _closures_using(node.body, _bound(node)))


if __name__ == "__main__":
    unittest.main()
