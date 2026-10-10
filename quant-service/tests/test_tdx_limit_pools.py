import asyncio
import inspect
import struct
import unittest
from datetime import date, datetime, timedelta, timezone
from unittest import mock

from app.datasources.catalog import BINDINGS
from app.datasources.contracts import UNSUPPORTED, CapabilityEvidence
from app.datasources.derived import limit_pools
from app.datasources.derived.limit_pools import derive_limit_pools, fetch_limit_pools
from app.datasources.resolver import _normalise_rows

TODAY = date(2026, 10, 12)
OBSERVED = datetime(2026, 10, 12, 1, 31, 5, tzinfo=timezone.utc)
POOLS = ("limit_up", "broken", "limit_down")


def f32(price):
    """A price as the MAC limit bits deliver it: the nearest float32 (12.96 becomes 12.960000038146973)."""
    return struct.unpack("<f", struct.pack("<f", price))[0]


def snapshot_row(symbol, price, high):
    return {"symbol": symbol, "price": price, "high": high}


def limit_row(symbol, up, down):
    return {"symbol": symbol, "trade_date": TODAY, "limit_up": f32(up), "limit_down": f32(down)}


def symbols_by_pool(members):
    return {pool: [row["symbol"] for row in members if row["pool"] == pool] for pool in POOLS}


# Limit prices as the MAC host delivered them for 2026-10-09 (scripts/data/tdx_mac_adapters_live_2026-10-10_mac*.json):
# 000001.SZ, 300750.SZ (a 20 % band), 600000.SH, 600004.SH and 600519.SH. The 600603.SH row is hand-built: a 5 % band
# around 5.13. So is the 688002.SH row: no evidence file shows how the MAC host marks a security without limits.
LIMITS = [
    limit_row("000001.SZ", 12.96, 10.60), limit_row("300750.SZ", 344.10, 229.40), limit_row("600603.SH", 5.39, 4.87),
    limit_row("600000.SH", 10.67, 8.73), limit_row("600519.SH", 1381.37, 1130.21), limit_row("600004.SH", 8.44, 6.90),
    limit_row("688002.SH", 0.0, 0.0),
]
SNAPSHOT = [
    snapshot_row("000001.SZ", 12.96, 12.96),      # sealed at the up limit
    snapshot_row("300750.SZ", 344.10, 344.10),    # sealed at a 20 % up limit
    snapshot_row("600603.SH", 5.39, 5.39),        # sealed at a 5 % up limit
    snapshot_row("600000.SH", 10.40, 10.67),      # touched the up limit and is below it now
    snapshot_row("600519.SH", 1130.21, 1180.00),  # sealed at the down limit
    snapshot_row("600004.SH", 8.43, 8.43),        # one tick below the up limit
    snapshot_row("688001.SH", 50.00, 50.00),      # no limit row
    snapshot_row("688002.SH", 44.00, 44.00),      # a limit row without a limit
]
EXPECTED = {"limit_up": ["000001.SZ", "300750.SZ", "600603.SH"], "broken": ["600000.SH"], "limit_down": ["600519.SH"]}


class LimitPoolRuleTests(unittest.TestCase):
    def test_pools_compare_prices_at_the_exchange_tick(self):
        members, _ = derive_limit_pools(SNAPSHOT, LIMITS, OBSERVED)
        self.assertEqual(symbols_by_pool(members), EXPECTED)
        self.assertIn({"pool": "limit_up", "symbol": "000001.SZ", "price": 12.96, "high": 12.96, "up_limit": 12.96,
                       "observed_at": OBSERVED}, members)
        self.assertIn({"pool": "broken", "symbol": "600000.SH", "price": 10.4, "high": 10.67, "up_limit": 10.67,
                       "observed_at": OBSERVED}, members)
        self.assertIn({"pool": "limit_down", "symbol": "600519.SH", "price": 1130.21, "high": 1180.0,
                       "down_limit": 1130.21, "observed_at": OBSERVED}, members)

    def test_securities_without_a_limit_are_counted(self):
        _, without_limit = derive_limit_pools(SNAPSHOT, LIMITS, OBSERVED)
        self.assertEqual(without_limit, 2)

    def test_a_row_without_a_positive_price_is_never_a_member(self):
        members, without_limit = derive_limit_pools(
            [snapshot_row("000001.SZ", 0.0, 12.96)], [limit_row("000001.SZ", 12.96, 10.60)], OBSERVED)
        self.assertEqual((members, without_limit), ([], 0))


class LimitPoolAdapterTests(unittest.TestCase):
    def fetch(self, trade_date, snapshot, limits):
        with (mock.patch.object(limit_pools, "cn_today", return_value=TODAY),
              mock.patch.object(limit_pools.tdx_legacy_misc, "fetch_all_a_snapshot", snapshot),
              mock.patch.object(limit_pools.tdx_mac, "fetch_limit_prices", limits)):
            return asyncio.run(fetch_limit_pools(trade_date=trade_date))

    def test_the_limits_of_the_snapshot_symbols_are_read_and_the_evidence_reports_the_later_read(self):
        later = OBSERVED + timedelta(seconds=7)
        requested = []

        async def snapshot():
            return CapabilityEvidence(SNAPSHOT, available_at_min=OBSERVED, available_at_max=OBSERVED,
                                      warnings=("tdx_host=snapshot-host:7709/login_one", "no_trade_rows=2"))

        async def limits(*, symbols):
            requested.append(symbols)
            return CapabilityEvidence(LIMITS, coverage=7 / 8, available_at_min=later, available_at_max=later,
                                      warnings=("tdx_host=limit-host:7709", "missing_symbols=1: 688001.SH"))

        evidence = self.fetch(TODAY, snapshot, limits)
        self.assertEqual(requested, [[row["symbol"] for row in SNAPSHOT]])
        self.assertEqual(symbols_by_pool(evidence.rows), EXPECTED)
        self.assertEqual({row["observed_at"] for row in evidence.rows}, {OBSERVED})
        self.assertEqual(evidence.coverage, 6 / 8)
        self.assertEqual((evidence.available_at_min, evidence.available_at_max), (later, later))
        self.assertEqual(evidence.warnings, ("tdx_host=snapshot-host:7709/login_one", "no_trade_rows=2",
                                             "tdx_host=limit-host:7709", "missing_symbols=1: 688001.SH",
                                             "limit_price_missing=2"))

    def test_a_date_other_than_the_current_session_is_refused_before_any_read(self):
        snapshot, limits = mock.AsyncMock(), mock.AsyncMock()
        with self.assertRaisesRegex(ValueError, r"only the current session \(2026-10-12\) can be derived, not 2026-10-09"):
            self.fetch(date(2026, 10, 9), snapshot, limits)
        snapshot.assert_not_awaited()
        limits.assert_not_awaited()


class LimitPoolBindingTests(unittest.TestCase):
    def test_the_three_bindings_are_unsupported_and_say_what_the_pools_lack(self):
        parameters = inspect.signature(fetch_limit_pools).parameters
        self.assertTrue(all(item.kind is inspect.Parameter.KEYWORD_ONLY for item in parameters.values()))
        members, _ = derive_limit_pools(SNAPSHOT, LIMITS, OBSERVED)
        for capability in ("limits.limit_up_pool", "limits.broken_pool", "limits.limit_down_pool"):
            binding = next(item for item in BINDINGS if (item.source, item.capability) == ("derived_tdx_limits", capability))
            self.assertEqual((binding.status, binding.decision_eligible), (UNSUPPORTED, False), capability)
            self.assertEqual(binding.adapter, f"app/datasources/derived/limit_pools.py:{fetch_limit_pools.__name__}")
            self.assertEqual(set(parameters), set(binding.spec.params), capability)
            for lack in ("首封/末封时间", "原因", "连板数", "封单额", "永不替代供应商池"):
                self.assertIn(lack, binding.notes, capability)
            projected = _normalise_rows(members, binding)
            self.assertEqual((projected.canonical, projected.status, projected.warnings), (True, None, ()), capability)


if __name__ == "__main__":
    unittest.main()
