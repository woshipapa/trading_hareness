"""The source-reader inventory and spec validation of the data-source layer (TDX plan P0)."""

import argparse
import contextlib
import io
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest import mock

from app.datasources import catalog
from app.datasources.catalog import BINDINGS
from app.datasources.completeness import (
    TDX_COMMAND_FAMILIES, completeness_problems, public_fetch_functions, referenced_by_bindings,
)
from app.datasources.contracts import DECLARED, Binding, BindingSpec


class CompletenessTests(unittest.TestCase):
    def _tree(self, files: dict[str, str]) -> tuple[Path, Path]:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        base = Path(tmp.name)
        for name, text in files.items():
            path = base / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        return base / "sources", base / "bindings.py"

    def test_readers_are_found_by_input_output_not_by_name(self):
        root, _ = self._tree({
            "sources/__init__.py": "",
            "sources/demo.py": (
                "import socket\nimport urllib.request\nfrom urllib.parse import urlencode\n\n"
                "def query_rows():\n    return urllib.request.urlopen('http://x').read()\n\n"
                "def uses_query():\n    return query_rows()\n\n"
                "def build(q):\n    return urlencode(q)\n\n"
                "def transform(rows):\n    return [row for row in rows]\n\n"
                "class QuoteProvider:\n    def quotes(self):\n        return self._raw()\n\n"
                "    def _raw(self):\n        return socket.create_connection(('h', 1))\n\n"
                "def uses_provider():\n    return QuoteProvider().quotes()\n"),
            "sources/sub/__init__.py": "",
            "sources/sub/deep.py": "async def load():\n    return []\n",
        })
        self.assertEqual(set(public_fetch_functions(root)),
                         {"demo.query_rows", "demo.uses_query", "demo.QuoteProvider.quotes", "demo.uses_provider",
                          "sub.deep.load"})

    def test_only_references_reachable_from_bind_count_as_bound(self):
        root, registry = self._tree({
            "sources/__init__.py": "",
            "sources/demo.py": ("async def fetch_live():\n    return []\n\nasync def fetch_dead():\n    return []\n\n"
                                "async def fetch_a():\n    return []\n\nasync def fetch_helper():\n    return []\n\n"
                                "FETCHERS = {'a': fetch_a}\n"),
            "bindings.py": (
                "from .sources import demo\n\n"
                "def _wrap():\n    return lambda: demo.fetch_helper()\n\n"
                "def register(resolver):\n"
                "    unused = demo.fetch_dead\n"
                "    resolver.bind('s', 'c', lambda: demo.fetch_live())\n"
                "    resolver.bind('s', 'd', _wrap())\n"
                "    for source, fetch in demo.FETCHERS.items():\n"
                "        resolver.bind(source, 'news.flash', fetch)\n"),
        })
        bound = referenced_by_bindings(registry, root)
        self.assertIn("demo.fetch_live", bound)
        self.assertIn("demo.fetch_helper", bound, "through a helper function")
        self.assertIn("demo.fetch_a", bound, "through the for-loop over a registry")
        self.assertNotIn("demo.fetch_dead", bound, "a reference outside the bind path does not count")

    def test_catalog_adapters_bind_only_functions_that_exist(self):
        root, registry = self._tree({
            "sources/__init__.py": "",
            "sources/demo.py": "async def fetch_via_catalog():\n    return []\n\nREPORT = 'RPT_DEMO'\n",
            "bindings.py": "from .sources import demo\n",
        })
        bindings = [Binding("tdx_public", "ticks.session", 20, DECLARED,
                            adapter="app/datasources/sources/demo.py:fetch_via_catalog"),
                    Binding("eastmoney_datacenter", "events.repurchase", 20, DECLARED,
                            adapter="app/datasources/sources/demo.py:RPT_DEMO"),
                    Binding("eastmoney_datacenter", "events.ipo_calendar", 20, DECLARED,
                            adapter="app/datasources/sources/demo.py:fetch_renamed_away")]
        families = {f"family:{name}": "later" for name in TDX_COMMAND_FAMILIES if name != "ticks"}
        problems = completeness_problems(bindings, root=root, bindings_module=registry, unregistered=families)
        self.assertNotIn("unregistered source fetch function: demo.fetch_via_catalog", problems)
        self.assertIn("catalog adapter app/datasources/sources/demo.py:fetch_renamed_away names no function in that module",
                      problems)
        self.assertFalse(any("RPT_DEMO" in item for item in problems), "a report id named in the module is accepted")

    def test_tdx_families_count_only_tdx_sources(self):
        root, registry = self._tree({"sources/__init__.py": "", "bindings.py": ""})
        others = {f"family:{name}": "later" for name in TDX_COMMAND_FAMILIES if name != "quote"}
        vendor = completeness_problems([Binding("fuyao_ths", "quote.all_a_snapshot", 20, DECLARED)],
                                       root=root, bindings_module=registry, unregistered=others)
        self.assertIn("unaccounted TDX command family: quote", vendor)
        tdx = completeness_problems([Binding("tdx_public", "quote.watch_snapshot", 20, DECLARED)],
                                    root=root, bindings_module=registry, unregistered=others)
        self.assertNotIn("unaccounted TDX command family: quote", tdx)

    def test_exemptions_need_a_reason_and_cannot_go_stale(self):
        root, registry = self._tree({
            "sources/__init__.py": "",
            "sources/demo.py": "async def fetch_bound():\n    return []\n\nasync def fetch_new():\n    return []\n\n"
                               "def pure(rows):\n    return rows\n",
            "bindings.py": "from .sources import demo\n\ndef register(resolver):\n"
                           "    resolver.bind('s', 'c', lambda: demo.fetch_bound())\n",
        })
        exemptions = {f"family:{name}": "later" for name in TDX_COMMAND_FAMILIES}
        exemptions.update({"demo.fetch_new": "  ", "demo.fetch_bound": "old exemption", "demo.pure": "not a reader",
                           "demo.fetch_gone": "renamed"})
        problems = completeness_problems([Binding("tdx_public", "ticks.session", 20, DECLARED)], root=root,
                                         bindings_module=registry, unregistered=exemptions)
        self.assertIn("UNREGISTERED entry demo.fetch_new has no reason", problems)
        self.assertIn("stale UNREGISTERED entry: demo.fetch_bound (bound now; delete the entry)", problems)
        self.assertIn("stale UNREGISTERED entry: demo.pure (not a reader under sources/)", problems)
        self.assertIn("stale UNREGISTERED entry: demo.fetch_gone (not a reader under sources/)", problems)
        self.assertIn("stale UNREGISTERED entry: family:ticks (a TDX binding covers it now; delete the entry)", problems)

    def test_the_production_inventory_is_complete(self):
        self.assertIn("tdx_local_files.write_csv", public_fetch_functions(), "found by its file write, not its name")
        self.assertEqual(completeness_problems(), [])


class SpecValidationTests(unittest.TestCase):
    def test_specs_name_canonical_fields_and_numeric_factors(self):
        grandfathered = next(item for item in BINDINGS if item.spec is None and item.source == "tencent_free")
        bad = Binding(grandfathered.source, grandfathered.capability, grandfathered.priority, grandfathered.status,
                      spec=BindingSpec(field_map={"x": "not_a_field"},
                                       unit_factors={"volume_lots": 100, "price": "100", "volume": Decimal("100")}))
        patched = tuple(bad if item is grandfathered else item for item in BINDINGS)
        with mock.patch.object(catalog, "BINDINGS", patched):
            problems = catalog.validate_catalog()
        label = f"binding {bad.source}->{bad.capability}"
        self.assertIn(f"grandfather entry {bad.source}->{bad.capability} has a BindingSpec now: delete it", problems)
        self.assertIn(f"{label}: field_map targets 'not_a_field', not a canonical field of the capability", problems)
        self.assertIn(f"{label}: unit factor for 'volume_lots', not a canonical field (factors apply after field_map)",
                      problems)
        self.assertIn(f"{label}: unit factor for 'price' must be a non-zero int or float, got '100'", problems)
        self.assertIn(f"{label}: unit factor for 'volume' must be a non-zero int or float, got Decimal('100')", problems)

    def test_catalog_cli_prints_bindings_that_carry_a_spec(self):
        from app.datasources import __main__ as cli
        binding = Binding("tdx_public", "ticks.session", 20, DECLARED, spec=BindingSpec(field_map={"price": "price"}))
        out = io.StringIO()
        with mock.patch.object(cli, "bindings_for", lambda *_args, **_kwargs: [binding]), contextlib.redirect_stdout(out):
            cli._catalog(argparse.Namespace(capability="ticks.session", source=None))
        self.assertIn('"field_map"', out.getvalue())


if __name__ == "__main__":
    unittest.main()
