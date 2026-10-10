"""The data-source catalog, the resolver and the strategy capability needs."""

import re
import unittest
from pathlib import Path

from app.datasources.catalog import (
    BINDINGS, CAPABILITIES, NON_SECTOR_GROUPS, NON_SECTOR_LABEL_PATTERN, SOURCES, bindings_for, catalog_document,
    evidence_locations, validate_catalog,
)
from app.datasources.contracts import RESOLVABLE_STATES, RETIRED, UNSUPPORTED
from app.datasources.completeness import completeness_problems, public_fetch_functions
from app.datasources.resolver import CapabilityResolver, CapabilityUnavailable
from app.platform.strategy_data_needs import STRATEGY_DATA_NEEDS, strategy_data_needs_catalog
from app.platform.strategy_registry import STRATEGY_CONTRACTS
from app.sector_membership_repository import sector_group_predicate

SERVICE_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = SERVICE_ROOT.parent


class CatalogTests(unittest.TestCase):
    def test_catalog_is_internally_consistent(self):
        self.assertEqual(validate_catalog(), [])

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

    def test_i2_legacy_bindings_are_unsupported_and_fully_specified(self):
        snapshot = next(item for item in BINDINGS
                        if item.source == "tdx_public" and item.capability == "quote.all_a_snapshot")
        overview = next(item for item in BINDINGS
                        if item.source == "tdx_public" and item.capability == "quote.index_overview")
        self.assertEqual((snapshot.status, overview.status), (UNSUPPORTED, UNSUPPORTED))
        self.assertFalse(snapshot.decision_eligible or overview.decision_eligible)
        self.assertEqual(snapshot.spec.max_batch, 80)
        self.assertEqual(snapshot.spec.unit_factors["volume"], 100)
        self.assertIn("排序宽度", overview.notes)

    def test_microstructure_bindings_are_unsupported_and_raw_auction_is_explicit(self):
        keys = {"microstructure.volume_profile", "microstructure.minute_series", "microstructure.auction_curve",
                "microstructure.unusual", "microstructure.top_board"}
        bindings = [item for item in BINDINGS if item.capability in keys]
        self.assertEqual({item.status for item in bindings}, {UNSUPPORTED})
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


if __name__ == "__main__":
    unittest.main()
