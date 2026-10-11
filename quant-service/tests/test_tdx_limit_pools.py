import asyncio
import dataclasses
import inspect
import struct
import unittest
from datetime import date, datetime, timedelta, timezone
from unittest import mock

from app.datasources import resolver as resolver_module
from app.datasources.catalog import BINDINGS
from app.datasources.contracts import DECLARED, LIVE_VERIFIED, UNSUPPORTED, CapabilityEvidence, CapabilityRequest
from app.datasources.derived import limit_pools
from app.datasources.derived.limit_pools import (
    derive_limit_pools, fetch_broken_pool, fetch_limit_down_pool, fetch_limit_up_pool)

TODAY = date(2026, 10, 12)
OBSERVED = datetime(2026, 10, 12, 1, 31, 5, tzinfo=timezone.utc)
LATER = OBSERVED + timedelta(seconds=7)
ADAPTERS = {"limit_up": ("limits.limit_up_pool", fetch_limit_up_pool),
            "broken": ("limits.broken_pool", fetch_broken_pool),
            "limit_down": ("limits.limit_down_pool", fetch_limit_down_pool)}


def f32(price):
    """A price as the MAC limit bits deliver it: the nearest float32 (12.96 becomes 12.960000038146973)."""
    return struct.unpack("<f", struct.pack("<f", price))[0]


def snapshot_row(symbol, price, high):
    return {"symbol": symbol, "price": price, "high": high}


def limit_row(symbol, up, down):
    return {"symbol": symbol, "trade_date": TODAY, "limit_up": f32(up), "limit_down": f32(down)}


# Limit prices as the MAC host delivered them for 2026-10-09
# (scripts/data/tdx_mac_adapters_live_2026-10-10_mac*.json): 000001.SZ, 300750.SZ (a 20 % band), 600000.SH, 600004.SH
# and 600519.SH. From scripts/data/tdx_mac_limits_all_a_2026-10-10_mac.json: the 301716.SZ row, a new listing (C鸿富诚)
# whose limits are 0.0 and 0.0 (its price is the file's, its high is set to the price). Hand-built: the 600603.SH row,
# a main-board ST band of 10 % around 5.50 (the file's up_ratio_by_board.main_st is 0.100 for 76 of its 137 rows, as
# app/market_rules.py says since 2026-07-06), and the 600005.SH row, the band of 600004.SH.
LIMITS = [
    limit_row("000001.SZ", 12.96, 10.60), limit_row("300750.SZ", 344.10, 229.40), limit_row("600603.SH", 6.05, 4.95),
    limit_row("600000.SH", 10.67, 8.73), limit_row("600519.SH", 1381.37, 1130.21), limit_row("600004.SH", 8.44, 6.90),
    limit_row("600005.SH", 8.44, 6.90), limit_row("301716.SZ", 0.0, 0.0),
]
SNAPSHOT = [
    snapshot_row("000001.SZ", 12.96, 12.96),      # sealed at the up limit
    snapshot_row("300750.SZ", 344.10, 344.10),    # sealed at a 20 % up limit
    snapshot_row("600603.SH", 6.05, 6.05),        # sealed at a 10 % up limit (main-board ST)
    snapshot_row("600000.SH", 10.40, 10.67),      # touched the up limit and is below it now
    snapshot_row("600519.SH", 1130.21, 1180.00),  # sealed at the down limit
    snapshot_row("600004.SH", 8.43, 8.43),        # one tick below the up limit
    snapshot_row("600005.SH", 6.91, 6.91),        # one tick above the down limit
    snapshot_row("688001.SH", 50.00, 50.00),      # no limit row
    snapshot_row("301716.SZ", 541.00, 541.00),    # a new listing: its limit row holds 0.0 and 0.0
]
EXPECTED = {"limit_up": ["000001.SZ", "300750.SZ", "600603.SH"], "broken": ["600000.SH"], "limit_down": ["600519.SH"]}


def symbols(rows):
    return [row["symbol"] for row in rows]


class FakeInputs:
    """The snapshot and limit-price readers, patched in for one run; ``calls`` records what was read."""

    def __init__(self, limit_rows=LIMITS, today=TODAY):
        self.limit_rows, self.today, self.calls = limit_rows, today, []

    async def snapshot(self):
        self.calls.append("snapshot")
        return CapabilityEvidence(SNAPSHOT, available_at_min=OBSERVED, available_at_max=OBSERVED,
                                  warnings=("tdx_host=snapshot-host:7709/login_one", "no_trade_rows=2"))

    async def limits(self, *, symbols):
        self.calls.append(("limits", symbols))
        return CapabilityEvidence(self.limit_rows, coverage=8 / 9, available_at_min=LATER, available_at_max=LATER,
                                  warnings=("tdx_host=limit-host:7709", "missing_symbols=1: 688001.SH"))

    def run(self, call, **params):
        with (mock.patch.object(limit_pools, "cn_today", return_value=self.today),
              mock.patch.object(limit_pools.tdx_legacy_misc, "fetch_all_a_snapshot", self.snapshot),
              mock.patch.object(limit_pools.tdx_mac, "fetch_limit_prices", self.limits)):
            return asyncio.run(call(**params))


def derived_binding(capability):
    return next(item for item in BINDINGS if (item.source, item.capability) == ("derived_tdx_limits", capability))


class LimitPoolRuleTests(unittest.TestCase):
    def test_pools_compare_prices_at_the_exchange_tick(self):
        pools, _ = derive_limit_pools(SNAPSHOT, LIMITS, OBSERVED)
        self.assertEqual({pool: symbols(rows) for pool, rows in pools.items()}, EXPECTED)
        self.assertIn({"symbol": "000001.SZ", "price": 12.96, "high": 12.96, "up_limit": 12.96,
                       "observed_at": OBSERVED}, pools["limit_up"])
        self.assertIn({"symbol": "600000.SH", "price": 10.4, "high": 10.67, "up_limit": 10.67,
                       "observed_at": OBSERVED}, pools["broken"])
        self.assertIn({"symbol": "600519.SH", "price": 1130.21, "high": 1180.0, "down_limit": 1130.21,
                       "observed_at": OBSERVED}, pools["limit_down"])

    def test_securities_without_a_limit_are_counted(self):
        _, without_limit = derive_limit_pools(SNAPSHOT, LIMITS, OBSERVED)
        self.assertEqual(without_limit, 2)

    def test_a_row_without_a_positive_price_is_never_a_member(self):
        result = derive_limit_pools([snapshot_row("000001.SZ", 0.0, 12.96)], [limit_row("000001.SZ", 12.96, 10.60)], OBSERVED)
        self.assertEqual(result, ({"limit_up": [], "broken": [], "limit_down": []}, 0))


class LimitPoolAdapterTests(unittest.TestCase):
    def test_the_private_reader_reads_each_input_once_and_the_three_pools_share_the_evidence(self):
        inputs = FakeInputs()
        evidences = inputs.run(limit_pools._read_limit_pools, trade_date=TODAY)
        self.assertEqual(inputs.calls, ["snapshot", ("limits", symbols(SNAPSHOT))])
        self.assertEqual({pool: symbols(evidence.rows) for pool, evidence in evidences.items()}, EXPECTED)
        self.assertEqual({row["observed_at"] for evidence in evidences.values() for row in evidence.rows}, {OBSERVED})
        for pool, evidence in evidences.items():
            self.assertEqual(evidence.coverage, 7 / 9, pool)
            self.assertEqual((evidence.available_at_min, evidence.available_at_max), (LATER, LATER), pool)
            self.assertEqual(evidence.warnings, ("tdx_host=snapshot-host:7709/login_one", "no_trade_rows=2",
                                                 "tdx_host=limit-host:7709", "missing_symbols=1: 688001.SH",
                                                 "limit_price_missing=2"), pool)

    def test_each_adapter_returns_the_rows_of_its_own_pool_with_the_shared_evidence(self):
        evidences = FakeInputs().run(limit_pools._read_limit_pools, trade_date=TODAY)
        for pool, (_, adapter) in ADAPTERS.items():
            self.assertEqual(FakeInputs().run(adapter, trade_date=TODAY), evidences[pool], pool)

    def test_a_date_other_than_the_current_session_is_refused_before_any_read(self):
        inputs = FakeInputs()
        with self.assertRaisesRegex(ValueError, r"only the current session \(2026-10-12\) can be derived, not 2026-10-09"):
            inputs.run(fetch_limit_up_pool, trade_date=date(2026, 10, 9))
        self.assertEqual(inputs.calls, [])

    def test_limit_rows_of_another_session_fail_naming_the_dates_and_the_count(self):
        earlier = [{**LIMITS[1], "trade_date": date(2026, 10, 8)}, {**LIMITS[2], "trade_date": date(2026, 10, 9)},
                   {**LIMITS[3], "trade_date": date(2026, 10, 8)}]
        with self.assertRaisesRegex(ValueError, r"3 of 8 limit rows are not dated 2026-10-12: 2026-10-08 \(2\), 2026-10-09 \(1\)"):
            FakeInputs([LIMITS[0], *earlier, *LIMITS[4:]]).run(fetch_limit_up_pool, trade_date=TODAY)
        saturday = date(2026, 10, 10)  # the host still answers with Friday's session
        friday = [{**row, "trade_date": date(2026, 10, 9)} for row in LIMITS]
        with self.assertRaisesRegex(ValueError, r"8 of 8 limit rows are not dated 2026-10-10: 2026-10-09 \(8\)"):
            FakeInputs(friday, today=saturday).run(fetch_limit_up_pool, trade_date=saturday)


class LimitPoolBindingTests(unittest.TestCase):
    def test_each_binding_names_its_adapter_and_says_what_the_pools_lack(self):
        for capability, adapter in ADAPTERS.values():
            binding = derived_binding(capability)
            parameters = inspect.signature(adapter).parameters
            self.assertTrue(all(item.kind is inspect.Parameter.KEYWORD_ONLY for item in parameters.values()), capability)
            self.assertEqual(binding.adapter, f"app/datasources/derived/limit_pools.py:{adapter.__name__}", capability)
            self.assertEqual(set(parameters), set(binding.spec.params), capability)
            self.assertIn(binding.status, {UNSUPPORTED, DECLARED, LIVE_VERIFIED}, capability)
            self.assertFalse(binding.decision_eligible, capability)
            for lack in ("首封/末封时间", "原因", "连板数", "封单额", "永不替代供应商池"):
                self.assertIn(lack, binding.notes, capability)

    def test_a_promoted_resolver_call_returns_the_rows_of_its_own_pool_only(self):
        for pool, (capability, adapter) in ADAPTERS.items():
            binding = dataclasses.replace(derived_binding(capability), status=DECLARED)
            request = CapabilityRequest(capability=capability, required_fields=("symbol",))
            with mock.patch.object(resolver_module, "bindings_for", lambda *args, **kwargs: [binding]):
                resolver = resolver_module.CapabilityResolver()
                resolver.bind("derived_tdx_limits", capability, adapter)
                result = FakeInputs().run(lambda **params: resolver.fetch(capability, request=request, **params),
                                          trade_date=TODAY)
            self.assertEqual(symbols(result.rows), EXPECTED[pool], capability)
            self.assertEqual((result.quality.status, result.quality.schema), ("partial", "canonical"), capability)


if __name__ == "__main__":
    unittest.main()
