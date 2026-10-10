"""The data-source catalog, the resolver and the strategy capability needs."""

import re
import unittest
from pathlib import Path
from unittest import mock

from app.datasources import catalog as catalog_module
from app.datasources.catalog import (
    BINDINGS, CAPABILITIES, NON_SECTOR_GROUPS, NON_SECTOR_LABEL_PATTERN, SOURCES, bindings_for, catalog_document,
    evidence_locations, validate_catalog,
)
from app.datasources.contracts import DECLARED, LIVE_VERIFIED, RESOLVABLE_STATES, RETIRED, UNSUPPORTED
from app.datasources.completeness import completeness_problems, public_fetch_functions
from app.datasources.resolver import CapabilityResolver, CapabilityUnavailable
from app.platform.strategy_data_needs import STRATEGY_DATA_NEEDS, strategy_data_needs_catalog
from app.platform.strategy_registry import STRATEGY_CONTRACTS
from app.sector_membership_repository import sector_group_predicate

SERVICE_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = SERVICE_ROOT.parent
#: The statuses a TDX binding passes through; promoting one (scripts/tdx-promote.py) must not break a test.
PROMOTION_PATH = frozenset({UNSUPPORTED, DECLARED, LIVE_VERIFIED})


class CatalogTests(unittest.TestCase):
    def test_catalog_is_internally_consistent(self):
        self.assertEqual(validate_catalog(), [])

    def test_catalog_rejects_zero_or_retired_only_bindings(self):
        from unittest import mock
        from app.datasources import catalog

        without_quote = tuple(item for item in BINDINGS if item.capability != "quote.all_a_snapshot")
        with mock.patch.object(catalog, "BINDINGS", without_quote):
            self.assertIn("quote.all_a_snapshot: no resolvable binding", catalog.validate_catalog())

        retired_only = tuple(item for item in BINDINGS if item.capability != "bars.daily")
        retired = next(item for item in BINDINGS if item.source == "longhuvip" and item.capability == "bars.daily")
        with mock.patch.object(catalog, "BINDINGS", retired_only + (retired,)):
            self.assertIn("bars.daily: no resolvable binding", catalog.validate_catalog())

    def test_catalog_allows_unsupported_only_binding(self):
        from unittest import mock
        from app.datasources import catalog

        target = "quote.all_a_snapshot"
        unsupported = next(item for item in BINDINGS if item.source == "eastmoney_free" and item.capability == target)
        bindings = tuple(item for item in BINDINGS if item.capability != target) + (unsupported,)
        with mock.patch.object(catalog, "BINDINGS", bindings):
            self.assertNotIn(f"{target}: no resolvable binding", catalog.validate_catalog())

    def test_capabilities_carry_a_canonical_schema(self):
        self.assertEqual(CAPABILITIES["quote.watch_snapshot"].schema.names,
                         tuple(field.split(":", 1)[0] for field in CAPABILITIES["quote.watch_snapshot"].fields))
        units = {item.name: item.unit for item in CAPABILITIES["ticks.session"].schema.fields}
        self.assertEqual((units["price"], units["volume"]), ("yuan", "shares"))

    def test_the_grandfather_list_is_literal_and_can_only_shrink(self):
        from unittest import mock

        from app.datasources import catalog
        from app.datasources.contracts import Binding, DECLARED
        self.assertLessEqual(len(catalog.GRANDFATHER_BINDINGS), catalog.GRANDFATHER_BASELINE_SIZE)
        self.assertTrue(all((item.source, item.capability) in catalog.GRANDFATHER_BINDINGS
                            for item in BINDINGS if item.spec is None))
        # Swapping one spec-less binding for another keeps the count and must still fail.
        dropped = next(item for item in BINDINGS if item.spec is None and item.source == "tencent_free")
        swapped = tuple(item for item in BINDINGS if item is not dropped) + (
            Binding("tencent_free", "events.repurchase", 90, DECLARED),)
        with mock.patch.object(catalog, "BINDINGS", swapped):
            problems = catalog.validate_catalog()
        self.assertIn("binding tencent_free->events.repurchase: missing BindingSpec outside grandfather list", problems)
        self.assertIn(f"grandfather entry tencent_free->{dropped.capability} has no binding: delete it (the list only shrinks)",
                      problems)

    def test_every_public_reader_is_bound_or_registered_with_a_reason(self):
        self.assertTrue(public_fetch_functions())
        self.assertEqual(completeness_problems(), [])

    def test_tushare_ths_boards_are_retired_and_fuyao_stays_declared(self):
        membership = {binding.source: binding for binding in BINDINGS if binding.capability == "sector.membership"}
        self.assertEqual(membership["tushare_super_get"].status, RETIRED)
        self.assertEqual(membership["fuyao_ths"].status, "declared")
        self.assertEqual(membership["fuyao_ths"].adapter, "app/fuyao_ths_membership.py:run_batch")
        self.assertNotIn("tushare_super_get", [binding.source for binding in bindings_for("sector.membership")])
        self.assertTrue(all(binding.status == RETIRED for binding in BINDINGS
                            if binding.source.startswith("tushare") and binding.capability.startswith("sector.")))

    def test_every_adapter_and_module_exists(self):
        for binding in BINDINGS:
            if binding.adapter:
                path = binding.adapter.split(":", 1)[0]
                if path.startswith("scripts/"):
                    if not (REPO_ROOT / "quant-service").is_dir():
                        continue   # outside the repository checkout (service image)
                    root = REPO_ROOT
                else:
                    root = SERVICE_ROOT
                self.assertTrue((root / path).is_file(), f"{binding.source}->{binding.capability}: {path}")
        for source in SOURCES.values():
                self.assertTrue((SERVICE_ROOT / source.module).is_file(), source.module)

    def test_owner_factor_binding_is_explicitly_peer_blocked(self):
        from app.longhu_shared_full_market import owner_factor_task

        with self.assertRaisesRegex(RuntimeError, "owner-only"):
            owner_factor_task()

    def test_credentials_are_names_never_values(self):
        for source in SOURCES.values():
            for name in source.credential_env:
                self.assertRegex(name, r"^[A-Z][A-Z0-9_]+$")

    def test_resolution_order_and_exclusions(self):
        order = [binding.source for binding in bindings_for("limits.limit_up_pool")]
        self.assertEqual(order[0], "fuyao_ths")
        self.assertNotIn("longhuvip", [binding.source for binding in bindings_for("bars.daily")])   # retired K-line
        self.assertNotIn("eastmoney_free", [binding.source for binding in bindings_for("quote.all_a_snapshot")])
        self.assertTrue(all(binding.status in RESOLVABLE_STATES for capability in CAPABILITIES
                            for binding in bindings_for(capability)))

    def test_evidence_locations_replace_hard_coded_vendor_filters(self):
        locations = evidence_locations("limits.limit_up_pool")
        self.assertEqual(locations[0], {"source": "fuyao_ths", "table": "market_events",
                                        "filter": {"event_type": "limit_up_pool"}})
        self.assertIn({"source": "eastmoney_ztb", "table": "raw_market_observations",
                       "filter": {"capability": "limit_pool_limit_up"}}, locations)

    def test_document_lists_retired_and_unsupported_separately(self):
        document = catalog_document()
        self.assertIn({"source": "longhuvip", "capability": "bars.daily",
                       "notes": "个股日K 接口（旧系统 id=7）恒空，下线；日K 走 longhuvip_composite"}, document["retired"])
        snapshot = next(item for item in document["capabilities"] if item["key"] == "quote.all_a_snapshot")
        self.assertIn(UNSUPPORTED, {provider["status"] for provider in snapshot["providers"]})
        self.assertNotIn(RETIRED, {provider["status"] for capability in document["capabilities"]
                                   for provider in capability["providers"]})

    def test_agreement_entries_are_checked(self):
        from app.datasources.contracts import Binding, BindingSpec

        def problems(field="close", drop=(), **changes):
            entry = {"reference": "tencent_free", "reference_adapter": "app/free_market_providers.py:tencent_intraday_minutes",
                     "reference_params": {"symbol": "symbol"}, "key": ["symbol", "bar_time"], "rel_tol": 0.001, "min_coverage": 0.9}
            entry = {key: value for key, value in {**entry, **changes}.items() if key not in drop}
            spec = BindingSpec(params={"symbol": "market+code", "count": "count"}, agreement={field: entry})
            return "\n".join(catalog_module._spec_problems(Binding("tdx_mac", "bars.minute", 70, UNSUPPORTED, spec=spec)))

        self.assertEqual(problems(), "")
        self.assertIn("'vwap' is not a canonical field", problems(field="vwap"))
        self.assertIn("unknown keys ['reltol']", problems(reltol=0.1))
        self.assertIn("reference 'nobody' is not a catalogued source", problems(reference="nobody"))
        self.assertIn("eastmoney_ztb has no binding for bars.minute", problems(reference="eastmoney_ztb", drop=("reference_adapter",)))
        self.assertEqual(problems(reference="eastmoney_ztb"), "", "a reader named by its adapter needs no binding of its source")
        self.assertIn("rel_tol must be a number that is not negative", problems(rel_tol=-1))
        self.assertIn("abs_tol must be a number that is not negative", problems(abs_tol="1"))
        self.assertIn("min_coverage must be a number above 0 and at most 1", problems(min_coverage=0))
        self.assertIn("min_coverage must be a number above 0 and at most 1", problems(min_coverage=1.5))
        self.assertIn("key must list canonical fields", problems(key=[]))
        self.assertIn("key must list canonical fields", problems(key=["symbol", "vwap"]))
        self.assertIn("reference_params must map keywords to parameters of the binding ['count', 'symbol']",
                      problems(reference_params={"symbol": "ticker"}))
        self.assertIn("reference_fixed must be a mapping", problems(reference_fixed=["pool"]))

    def test_every_reference_reader_of_an_agreement_accepts_what_the_check_gives_it(self):
        import importlib
        import inspect

        from app.datasources.contracts import reference_keywords

        entries = [(item, entry) for item in BINDINGS if item.spec for entry in item.spec.agreement.values()
                   if entry.get("reference_adapter")]
        self.assertTrue(entries)
        for item, entry in entries:
            path, _, name = entry["reference_adapter"].partition(":")
            parameters = inspect.signature(getattr(importlib.import_module(path.removesuffix(".py").replace("/", ".")), name)).parameters
            given = set(reference_keywords(entry, dict.fromkeys(item.spec.params)))
            required = {key for key, parameter in parameters.items() if parameter.default is parameter.empty
                        and parameter.kind not in (parameter.VAR_POSITIONAL, parameter.VAR_KEYWORD)}
            where = f"{item.source}->{item.capability} {entry['reference_adapter']}"
            self.assertLessEqual(given, set(parameters), where)
            self.assertLessEqual(required, given, where)

    def test_i2_legacy_bindings_are_fully_specified_whatever_their_promotion_state(self):
        snapshot = next(item for item in BINDINGS
                        if item.source == "tdx_public" and item.capability == "quote.all_a_snapshot")
        overview = next(item for item in BINDINGS
                        if item.source == "tdx_public" and item.capability == "quote.index_overview")
        for binding in (snapshot, overview):
            self.assertIn(binding.status, PROMOTION_PATH)
            self.assertIsNotNone(binding.spec)
            self.assertFalse(binding.decision_eligible)
        self.assertEqual(snapshot.spec.max_batch, 80)
        self.assertEqual(snapshot.spec.unit_factors["volume"], 100)
        self.assertIn("排序宽度", overview.notes)

    def test_microstructure_bindings_are_specified_and_raw_auction_is_explicit(self):
        keys = {"microstructure.volume_profile", "microstructure.minute_series", "microstructure.auction_curve",
                "microstructure.unusual", "microstructure.top_board"}
        bindings = [item for item in BINDINGS if item.capability in keys]
        self.assertEqual({item.capability for item in bindings}, keys)
        self.assertLessEqual({item.status for item in bindings}, PROMOTION_PATH)
        self.assertTrue(all(item.spec is not None and not item.decision_eligible for item in bindings))
        auction = CAPABILITIES["microstructure.auction_curve"]
        self.assertEqual(auction.schema.names, ("time", "price", "matched_raw", "unmatched_raw", "unmatched_side"))
        self.assertFalse(any(item.source == "tdx_public" and item.capability == "bars.minute" for item in BINDINGS))


class NonSectorGroupTests(unittest.TestCase):
    """Qualification lists never stand in for a sector (2026-09-18 labels)."""

    #: Kept on purpose: members share a driver, so they trade together.
    SECTORS = ("国企改革", "央企国企改革", "中字头股票", "参股券商", "国家大基金持股", "ST板块",
               "新股与次新股", "注册制次新股", "摘帽", "股权转让(并购重组)", "芯片概念", "智能电网", "人民币贬值受益",
               "粤港澳大湾区", "5G", "6G概念", "3D打印", "PM2.5", "AI PC")
    #: Lists THS mints later and that no key names yet.
    FUTURE_LISTS = ("2026三季报预增", "2026年报预增", "2027一季报预减", "中证1000成份股", "深证100成份股",
                    "同花顺红利50", "同花顺AI指数", "转融券标的", "港股通(深)")

    def test_keys_are_ths_codes_with_labels(self):
        for key, label in NON_SECTOR_GROUPS.items():
            self.assertRegex(key, r"^88\d{4}\.TI$")
            self.assertTrue(label.strip(), key)

    def test_pattern_catches_future_lists_and_spares_sectors(self):
        pattern = re.compile(NON_SECTOR_LABEL_PATTERN)
        for label in self.FUTURE_LISTS:
            self.assertRegex(label, pattern)
        for label in self.SECTORS:
            self.assertIsNone(pattern.search(label), label)
        self.assertFalse(set(NON_SECTOR_GROUPS.values()) & set(self.SECTORS))

    def test_predicate_binds_one_parameter_per_placeholder(self):
        sql, parameters = sector_group_predicate("m")
        self.assertEqual(sql.count("%s"), len(parameters))
        self.assertIn("m.sector_key", sql)
        self.assertNotIn("member.", sql)
        self.assertEqual(parameters, (list(NON_SECTOR_GROUPS), NON_SECTOR_LABEL_PATTERN))

    def test_document_publishes_the_list(self):
        self.assertEqual(catalog_document()["non_sector_groups"]["keys"]["885338.TI"], "融资融券")


class StrategyNeedsTests(unittest.TestCase):
    def test_every_strategy_states_resolvable_capabilities(self):
        self.assertEqual(set(STRATEGY_DATA_NEEDS), set(STRATEGY_CONTRACTS))
        for strategy, needs in STRATEGY_DATA_NEEDS.items():
            self.assertTrue(needs.needs, strategy)
            for need in needs.needs:
                self.assertIn(need.capability, CAPABILITIES, f"{strategy}: {need.capability}")
                if need.required:
                    self.assertTrue(bindings_for(need.capability), f"{strategy} needs {need.capability}")

    def test_needs_never_name_a_vendor(self):
        vendors = set(SOURCES)
        for item in strategy_data_needs_catalog():
            for need in item["needs"]:
                self.assertFalse(any(vendor in need["capability"] for vendor in vendors))

    def test_legacy_couplings_point_at_real_lines(self):
        for needs in STRATEGY_DATA_NEEDS.values():
            for coupling in needs.legacy_couplings:
                match = re.match(r"^(app/[\w/]+\.py):(\d+) ", coupling)
                self.assertIsNotNone(match, coupling)
                lines = (SERVICE_ROOT / match.group(1)).read_text(encoding="utf-8").splitlines()
                self.assertLessEqual(int(match.group(2)), len(lines), coupling)


class MigrationPinTests(unittest.TestCase):
    """What the catalog resolves to must equal what strategy code used to say."""

    def test_taxonomies_equal_the_literals_they_replaced(self):
        from app.datasources.catalog import TAXONOMIES
        from app.platform.strategy_data_needs import strategy_taxonomies

        # Reordered 2026-09-18 after the membership load filled both maps:
        # coverage then favoured 23.6 concepts per stock over one industry.
        # fuyao_ths_concept joined on 2026-10-09: ths_concept_flow is no longer
        # refreshed (Tushare retired), the Fuyao map is, and it is read first.
        self.assertEqual(strategy_taxonomies("xiaojie_leader_flow"),
                         ("longhu_ths_industry", "fuyao_ths_concept", "ths_concept_flow"))
        self.assertEqual(strategy_taxonomies("intraday_watchlist_confirmation"),
                         ("fuyao_ths_concept", "ths_concept_flow", "ths_index_n", "ths_industry"))
        self.assertEqual(strategy_taxonomies("countertrend_rebound_shadow"), ("ths_industry",))
        for needs in STRATEGY_DATA_NEEDS.values():
            for need in needs.needs:
                if not need.taxonomies:
                    continue
                statuses = {key: TAXONOMIES[key].status for key in need.taxonomies}
                # A declared map is only ever a candidate beside verified ones,
                # never what a strategy rests on alone.
                self.assertTrue(set(statuses.values()) <= {"live_verified", "declared"}, statuses)
                self.assertIn("live_verified", statuses.values(), statuses)
                if "ths_concept_flow" in need.taxonomies:
                    keys = list(need.taxonomies)
                    self.assertEqual(keys.index("fuyao_ths_concept"), keys.index("ths_concept_flow") - 1, keys)

    def test_label_properties_and_store_order(self):
        from app.datasources.catalog import (
            EXCHANGE_TIMESTAMPED_QUOTE_LABELS, RULE_USABLE_FLOW_LABELS, primary_source, primary_store_value,
            store_values, taxonomies_for,
        )

        self.assertEqual(EXCHANGE_TIMESTAMPED_QUOTE_LABELS, {"tencent_batched_watch_quote", "longhuvip_watch_quote"})
        self.assertEqual(RULE_USABLE_FLOW_LABELS, {"fuyao_ths_derived"})
        self.assertEqual(primary_source("quote.all_a_snapshot"), "fuyao_ths")
        self.assertEqual(primary_store_value("flow.stock_daily", "stock_money_flow_daily", "source"), "longhuvip_main_net")
        self.assertEqual(store_values("bars.adjustment_factor", "daily_adjustment_factors", "provider")[:2],
                         ("longhu_qfq_derived", "tushare_super_sdk"))
        self.assertEqual(store_values("fundamentals.daily_basic", "daily_fundamentals", "provider"),
                         ("tushare_super_get", "longhuvip_composite"))
        self.assertNotIn("fuyao_ths_concept", taxonomies_for(["ths_concept"]))       # declared, not proven
        self.assertIn("fuyao_ths_concept", taxonomies_for(["ths_concept"], min_status="declared"))

    def test_order_book_storage_declares_real_relation_and_source(self):
        longhu = [b for b in bindings_for("quote.order_book") if b.source == "longhuvip"][0]
        tencent = [b for b in bindings_for("quote.order_book") if b.source == "tencent_free"][0]
        self.assertEqual(longhu.store, "intraday_quote_observations:source_name=longhu_order_book")
        self.assertEqual(tencent.store, "intraday_quote_observations:source_name=tencent_order_book")
        self.assertNotIn("intraday_order_book_observations", longhu.store)

    def test_health_capability_aliases_follow_the_binding_catalog(self):
        from app.datasources.catalog import health_capability
        self.assertEqual(health_capability("fuyao_ths", "quote.all_a_snapshot"), "a_share_prices_snapshot")
        self.assertEqual(health_capability("longhuvip", "quote.watch_snapshot"), "stock_quote")
        self.assertEqual(health_capability("tencent_free", "quote.order_book"), "order_book_quote")


class ValidateRuleTests(unittest.TestCase):
    """Test that validation rule handles UNSUPPORTED bindings correctly."""

    def test_capability_with_zero_bindings_fails_validation(self):
        """Capability with no bindings at all should be flagged."""
        from unittest import mock
        from app.datasources import catalog
        from app.datasources.contracts import Capability, CanonicalSchema, FieldSpec

        # Create a fake capability with no bindings
        test_capability = Capability("test.phantom", "test", "Test", "daily", "all_a", ("field1",), "", "",
                                     CanonicalSchema((FieldSpec("field1"),)))
        test_bindings = tuple(b for b in BINDINGS if b.capability != "test.phantom")

        with mock.patch.object(catalog, "CAPABILITIES", {**CAPABILITIES, "test.phantom": test_capability}):
            with mock.patch.object(catalog, "BINDINGS", test_bindings):
                problems = catalog.validate_catalog()
        self.assertIn("test.phantom: no resolvable binding", problems)

    def test_capability_with_only_retired_bindings_fails_validation(self):
        """Capability with only RETIRED bindings should be flagged."""
        from unittest import mock
        from app.datasources import catalog
        from app.datasources.contracts import Binding, RETIRED

        test_bindings = list(BINDINGS)
        # Add a capability with only RETIRED binding
        test_bindings.append(Binding("tdx_public", "test.phantom", 70, RETIRED))

        test_capability = CAPABILITIES.get("test.phantom")
        if not test_capability:
            from app.datasources.contracts import Capability, CanonicalSchema, FieldSpec
            test_capability = Capability("test.phantom", "test", "Test", "daily", "all_a", ("field1",), "", "",
                                         CanonicalSchema((FieldSpec("field1"),)))

        with mock.patch.object(catalog, "CAPABILITIES", {**CAPABILITIES, "test.phantom": test_capability}):
            with mock.patch.object(catalog, "BINDINGS", tuple(test_bindings)):
                problems = catalog.validate_catalog()
        self.assertIn("test.phantom: no resolvable binding", problems)

    def test_capability_with_only_unsupported_bindings_passes_validation(self):
        """Capability with only UNSUPPORTED bindings should NOT be flagged."""
        from unittest import mock
        from app.datasources import catalog
        from app.datasources.contracts import Binding, UNSUPPORTED

        test_bindings = list(BINDINGS)
        # Add a capability with only UNSUPPORTED binding
        test_bindings.append(Binding("tdx_mac", "test.phantom", 70, UNSUPPORTED))

        test_capability = CAPABILITIES.get("test.phantom")
        if not test_capability:
            from app.datasources.contracts import Capability, CanonicalSchema, FieldSpec
            test_capability = Capability("test.phantom", "test", "Test", "daily", "all_a", ("field1",), "", "",
                                         CanonicalSchema((FieldSpec("field1"),)))

        with mock.patch.object(catalog, "CAPABILITIES", {**CAPABILITIES, "test.phantom": test_capability}):
            with mock.patch.object(catalog, "BINDINGS", tuple(test_bindings)):
                problems = catalog.validate_catalog()
        # Should NOT be in problems - UNSUPPORTED bindings are allowed
        binding_problems = [p for p in problems if "test.phantom" in p and "no resolvable binding" in p]
        self.assertEqual(len(binding_problems), 0)

    def test_production_catalog_validates_clean(self):
        """The production catalog should have no validation problems."""
        self.assertEqual(validate_catalog(), [])


class ResolverTests(unittest.IsolatedAsyncioTestCase):
    async def test_priority_fallback_and_provenance(self):
        resolver = CapabilityResolver()

        async def broken(**_params):
            raise RuntimeError("upstream down")

        async def backup(**params):
            return [{"symbol": "000001.SZ", "day": params["trade_date"]}]

        resolver.bind("fuyao_ths", "limits.limit_up_pool", broken)
        resolver.bind("eastmoney_ztb", "limits.limit_up_pool", backup)
        result = await resolver.fetch("limits.limit_up_pool", trade_date="2026-09-18")
        self.assertEqual(result.source, "eastmoney_ztb")
        self.assertTrue(result.is_fallback)
        self.assertEqual([attempt["status"] for attempt in result.attempts], ["failed", "completed"])
        self.assertEqual(result.provenance()["capability"], "limits.limit_up_pool")

    async def test_empty_falls_through_unless_accepted(self):
        resolver = CapabilityResolver()

        async def empty(**_params):
            return []

        resolver.bind("fuyao_ths", "limits.limit_down_pool", empty)
        with self.assertRaises(CapabilityUnavailable) as raised:
            await resolver.fetch("limits.limit_down_pool", trade_date="2026-09-18")
        self.assertEqual(raised.exception.attempts[0]["status"], "empty")
        result = await resolver.fetch("limits.limit_down_pool", accept_empty=True, trade_date="2026-09-18")
        self.assertEqual(result.rows, [])

    async def test_health_gate_and_source_restriction(self):
        async def gate(source, _capability):
            return source != "fuyao_ths"

        resolver = CapabilityResolver(health_gate=gate)

        async def rows(**_params):
            return [1]

        resolver.bind("fuyao_ths", "limits.broken_pool", rows)
        resolver.bind("eastmoney_ztb", "limits.broken_pool", rows)
        result = await resolver.fetch("limits.broken_pool", trade_date="d")
        self.assertEqual(result.source, "eastmoney_ztb")
        self.assertEqual(result.attempts[0], {"source": "fuyao_ths", "status": "circuit_open"})
        with self.assertRaises(CapabilityUnavailable):
            await resolver.fetch("limits.broken_pool", sources=["fuyao_ths"], trade_date="d")

    def test_bind_rejects_uncatalogued_or_refused_sources(self):
        resolver = CapabilityResolver()

        async def rows(**_params):
            return [1]

        with self.assertRaises(ValueError):
            resolver.bind("fuyao_ths", "no.such_capability", rows)
        with self.assertRaises(ValueError):
            resolver.bind("sina_free", "limits.limit_up_pool", rows)
        with self.assertRaises(ValueError):
            resolver.bind("eastmoney_free", "quote.all_a_snapshot", rows)   # unsupported
        with self.assertRaises(ValueError):
            resolver.bind("longhuvip", "bars.daily", rows)                  # retired

    def test_unsupported_is_never_routable_but_declared_is(self):
        resolver = CapabilityResolver()

        async def rows(**_params):
            return [{"symbol": "000001.SZ"}]

        with self.assertRaises(ValueError):
            resolver.bind("eastmoney_free", "quote.all_a_snapshot", rows)
        resolver.bind("tdx_public", "ticks.session", rows)
        self.assertIn("tdx_public", resolver.bound_sources("ticks.session"))

    def test_package_bindings_cover_their_catalog_entries(self):
        from app.datasources.bindings import register_package_sources

        async def fuyao(_route, _params):
            return {}

        resolver = register_package_sources(CapabilityResolver(), fuyao_fetch=fuyao)
        package_sources = {key for key, source in SOURCES.items() if source.module.startswith("app/datasources/sources/")
                           and source.license != "local_files"}
        for binding in BINDINGS:
            if binding.source in package_sources and binding.status in RESOLVABLE_STATES and binding.adapter \
                    and not binding.adapter.startswith(("app/datasources/collectors/", "scripts/")):
                self.assertIn(binding.source, resolver.bound_sources(binding.capability),
                              f"{binding.source}->{binding.capability} has no implementation bound")

    def test_promoting_a_binding_of_a_listed_module_needs_no_registration(self):
        import dataclasses

        from app.datasources import bindings

        def listed(item):
            module = item.adapter.partition(":")[0].removeprefix("app/datasources/").removesuffix(".py")
            return module in bindings.GENERIC_ADAPTER_MODULES

        flipped = tuple(dataclasses.replace(item, status=DECLARED) if item.adapter and ":" in item.adapter and listed(item)
                        and item.status in PROMOTION_PATH else item for item in BINDINGS)
        promoted = [item for item in flipped if item.status == DECLARED and item.adapter and listed(item)]
        self.assertIn("tdx_mac", {item.source for item in promoted})
        with mock.patch.object(catalog_module, "BINDINGS", flipped):
            resolver = bindings.register_package_sources(CapabilityResolver())
            for item in promoted:
                self.assertIn(item.source, resolver.bound_sources(item.capability), f"{item.source}->{item.capability}")

    def test_a_listed_module_that_is_not_written_yet_matters_only_to_a_resolvable_binding(self):
        from app.datasources import bindings
        from app.datasources.contracts import Binding

        adapter = "app/datasources/sources/not_written_yet.py:fetch"
        with mock.patch.object(bindings, "GENERIC_ADAPTER_MODULES", ("sources/not_written_yet",)):
            parked = (*BINDINGS, Binding("tdx_public", "quote.watch_snapshot", 80, UNSUPPORTED, adapter=adapter))
            with mock.patch.object(catalog_module, "BINDINGS", parked):
                bindings.register_package_sources(CapabilityResolver())
            promoted = (*BINDINGS, Binding("tdx_public", "quote.watch_snapshot", 80, DECLARED, adapter=adapter))
            with mock.patch.object(catalog_module, "BINDINGS", promoted), self.assertRaises(ModuleNotFoundError):
                bindings.register_package_sources(CapabilityResolver())


if __name__ == "__main__":
    unittest.main()
