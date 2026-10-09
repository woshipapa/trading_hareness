from __future__ import annotations

import unittest
from unittest.mock import patch
from datetime import date
from types import SimpleNamespace

from app.longhu_shared_full_market import (
    MINIMUM_PLATES,
    SharedLonghuFullMarketSource,
    gateway_workers,
    parse_catalog_row,
)
from app.longhu_market_service import minimum_full_market_rows

TRADE_DATE = date(2026, 9, 18)

# One real ranking row and one real member row, as the gateway returned them
# on 2026-09-18, trimmed to the columns the parsers read.
CATALOG_ROW = ["881121", "半导体", 1.2, 3.45, 0.8, 1.2e10, 4.5e8, 0, 0, 1.9]
MEMBER_ROW = ["301583", "托伦斯", "", 0, "次新股、半导体设备", 142.56, 20, 1675296901, 41.16, 0,
              4395320535, 777285562, -361020864, 416264698, 46.4, 21.55, 24.85, 17.68, 8.21,
              9.47, 0, 2.9723, 0, "首板", "", 41.16, "", 0, 100300792, 1256952704, ""]


def _page(rows, count=None):
    payload = {"list": list(rows)}
    if count is not None:
        payload["Count"] = count
    return {"pages": [{"payload": payload}]}


class _Gateway:
    """Answers the two documented actions and records what was asked."""

    def __init__(self, *, plates=1, members_per_plate=1, member_failures=()):
        self.plates = plates
        self.members_per_plate = members_per_plate
        self.member_failures = set(member_failures)
        self.requests: list[dict] = []

    def raw_call(self, request):
        self.requests.append(request)
        action = request["params"]["a"]
        if action == "RealRankingInfo":
            offset = int(request["params"]["Index"])
            if offset >= self.plates:
                return _page([], count=self.plates)
            rows = [[f"88{1120 + index}", f"板块{index}", 1.0, 2.0, 0.0, 1.0, 5.0, 0, 0, 1.0]
                    for index in range(offset, min(offset + 8, self.plates))]
            return _page(rows, count=self.plates)
        plate = request["params"]["PlateID"]
        if plate in self.member_failures:
            raise RuntimeError(f"gateway refused {plate}")
        offset = int(request["params"]["Index"])
        if offset >= self.members_per_plate:
            return _page([])
        rows = []
        for index in range(offset, min(offset + 300, self.members_per_plate)):
            row = list(MEMBER_ROW)
            row[0] = f"{301583 + index:06d}"
            rows.append(row)
        return _page(rows, count=self.members_per_plate)


class SharedLonghuFullMarketTests(unittest.TestCase):
    """A peer assembles the licensed close from the gateway, not from Tushare."""

    def test_a_ranking_row_is_read_into_the_owner_adapter_shape(self):
        parsed = parse_catalog_row(CATALOG_ROW)
        self.assertEqual(parsed["sector_key"], "881121")
        self.assertEqual(parsed["label"], "半导体")
        self.assertEqual(parsed["change_pct"], 3.45)
        self.assertEqual(parsed["net_inflow"], 4.5e8)
        self.assertEqual(parsed["taxonomy_key"], "longhu_ths_industry")

    def test_a_malformed_ranking_row_is_dropped_rather_than_guessed(self):
        self.assertIsNone(parse_catalog_row([]))
        self.assertIsNone(parse_catalog_row("not a row"))
        self.assertIsNone(parse_catalog_row(["   "]))

    def test_a_short_catalog_is_refused_because_it_shrinks_the_market(self):
        source = SharedLonghuFullMarketSource(_Gateway(plates=10))
        with self.assertRaises(RuntimeError) as raised:
            source.industry_plate_catalog()
        self.assertIn("coverage too small", str(raised.exception))

    def test_the_catalog_pages_until_the_vendor_count_is_reached(self):
        gateway = _Gateway(plates=104)
        catalog = SharedLonghuFullMarketSource(gateway).industry_plate_catalog()
        self.assertEqual(len(catalog), 104)
        self.assertGreaterEqual(len(catalog), MINIMUM_PLATES)
        self.assertEqual(len({row["sector_key"] for row in catalog}), 104)

    def test_members_are_read_in_the_historical_form_for_a_finished_session(self):
        gateway = _Gateway(plates=104, members_per_plate=2)
        rows = SharedLonghuFullMarketSource(gateway).plate_day("881121", TRADE_DATE)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["symbol"], "301583.SZ")
        self.assertEqual(rows[0]["pct_chg"], 20.0)
        member_call = next(r for r in gateway.requests if r["params"]["a"] == "ZhiShuStockList_W8")
        # The live parameter form is rejected by the vendor, so it must not appear.
        self.assertEqual(member_call["params"]["Date"], "2026-09-18")
        self.assertNotIn("RStart", member_call["params"])

    def test_a_refused_board_is_reported_as_lost_coverage_not_a_quiet_gap(self):
        gateway = _Gateway(plates=104, members_per_plate=1, member_failures={"881120"})
        _rows, health = SharedLonghuFullMarketSource(gateway, workers=2).full_market_vendor_rows(
            TRADE_DATE, plate_ids=["881120", "881121"],
        )
        self.assertEqual(health["successful_plates"], 1)
        self.assertEqual(health["plate_coverage"], 0.5)
        self.assertEqual(len(health["errors"]), 1)
        self.assertEqual(health["transport"], "shared_gateway")

    def test_full_market_evidence_carries_boards_rows_and_quote_health(self):
        gateway = _Gateway(plates=104, members_per_plate=1)
        source = SharedLonghuFullMarketSource(gateway, workers=2)
        source.tencent_quotes = lambda symbols: ([{"symbol": s} for s in symbols], {"coverage": 1.0})
        evidence = source.fetch_full_market_evidence(TRADE_DATE)
        self.assertEqual(evidence["trade_date"], TRADE_DATE)
        self.assertEqual(len(evidence["board_rows"]), 104)
        self.assertTrue(evidence["vendor_rows"])
        self.assertIn("longhu", evidence["health"])
        self.assertTrue(evidence["board_rows"][0]["source"].startswith("longhuvip_gateway:"))

    def test_the_worker_count_stays_bounded_whatever_the_environment_says(self):
        self.assertEqual(gateway_workers({"QUANT_LONGHU_GATEWAY_WORKERS": "0"}), 1)
        self.assertEqual(gateway_workers({"QUANT_LONGHU_GATEWAY_WORKERS": "999"}), 16)
        self.assertEqual(gateway_workers({"QUANT_LONGHU_GATEWAY_WORKERS": "nonsense"}), 4)

    def test_tencent_batches_report_coverage_instead_of_raising(self):
        class _Session:
            def get(self, _url, timeout=None):
                raise RuntimeError("public endpoint unreachable")

        source = SharedLonghuFullMarketSource(_Gateway(), session=_Session())
        rows, health = source.tencent_quotes(["600176.SH", "000001.SZ"])
        self.assertEqual(rows, [])
        self.assertEqual(health["coverage"], 0.0)
        self.assertEqual(len(health["errors"]), 1)

    def test_close_gate_uses_point_in_time_all_a_population(self):
        self.assertEqual(minimum_full_market_rows(5_569), 5_291)
        self.assertEqual(minimum_full_market_rows(3_000), 3_500)


class FullMarketTransportSelectionTests(unittest.TestCase):
    """One flag, two hosts: the licence decides the transport, not the operator."""

    def test_a_host_without_the_licence_uses_the_gateway(self):
        import app.main as main
        from app.longhu_shared_full_market import shared_longhu_source_factory
        with patch("app.longhu_vendor_source.direct_access_enabled", return_value=False):
            self.assertIs(main.longhu_full_market_source_factory(), shared_longhu_source_factory)

    def test_the_licensed_owner_still_talks_to_the_vendor_directly(self):
        import app.main as main
        from app.longhu_market_service import owner_longhu_source_factory
        with patch("app.longhu_vendor_source.direct_access_enabled", return_value=True):
            self.assertIs(main.longhu_full_market_source_factory(), owner_longhu_source_factory)


class LicensedClosePathWiringTests(unittest.TestCase):
    """The enabled path must be reachable, not merely written.

    ``sync_longhu_full_market_close`` was called in two places and imported in
    none.  The flag that reaches those branches defaults to off, so the module
    imported cleanly and the route raised NameError the first time anyone
    turned the licensed close on - in production, on the first attempt.
    """

    def test_every_name_the_enabled_branch_calls_actually_resolves(self):
        import app.main as main

        self.assertTrue(callable(main.sync_longhu_full_market_close))
        self.assertTrue(callable(main.owner_longhu_source_factory))
        self.assertTrue(callable(main.shared_longhu_source_factory))

    def test_the_full_market_route_hands_the_gateway_factory_to_the_licensed_sync(self):
        import asyncio

        import app.main as main
        from app.longhu_shared_full_market import shared_longhu_source_factory
        from app.request_models import FullMarketDailySyncRequest

        async def fake_sync(trade_date, **kwargs):
            return {"status": "completed", "trade_date": str(trade_date),
                    "source_factory": kwargs["source_factory"]}

        with patch("app.main.longhu_full_market_enabled", return_value=True), \
             patch("app.longhu_vendor_source.direct_access_enabled", return_value=False), \
             patch("app.main.sync_longhu_full_market_close", new=fake_sync):
            result = asyncio.run(main.sync_full_market_daily(
                FullMarketDailySyncRequest(provider="auto", trade_date=TRADE_DATE),
            ))
        self.assertEqual(result["status"], "completed")
        self.assertIs(result["source_factory"], shared_longhu_source_factory)

    def test_a_partial_longhu_result_is_returned_for_the_pipeline_s_own_fallback(self):
        import asyncio

        import app.main as main
        from app.request_models import FullMarketDailySyncRequest

        async def failed_longhu(*_args, **_kwargs):
            return {"status": "failed", "reason": "point-in-time all-A coverage"}

        with patch("app.main.longhu_full_market_enabled", return_value=True), \
             patch("app.main.longhu_full_market_source_factory", return_value=object()), \
             patch("app.main.sync_longhu_full_market_close", new=failed_longhu):
            result = asyncio.run(main.sync_full_market_daily(
                FullMarketDailySyncRequest(provider="auto", trade_date=TRADE_DATE),
            ))

        # The daily pipeline falls back to Baostock on a failed primary; the
        # retired Tushare chain is no longer tried in between.
        self.assertEqual(result, {"status": "failed", "reason": "point-in-time all-A coverage"})


if __name__ == "__main__":
    unittest.main()
