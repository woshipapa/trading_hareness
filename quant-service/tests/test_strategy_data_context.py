import unittest

from app.datasources.contracts import CapabilityRequest, QualityReceipt
from app.platform.strategy_data_context import (
    StrategyCapabilityValue,
    StrategyDataUnavailable,
    compile_strategy_data_plan,
    resolve_strategy_data,
)


class StrategyDataContextTests(unittest.IsolatedAsyncioTestCase):
    def test_plan_is_compiled_from_capabilities_not_vendors(self):
        plan = compile_strategy_data_plan(
            "countertrend_rebound_shadow",
            purpose="replay",
            as_of="2026-09-19T07:00:00+00:00",
            request_overrides={"bars.daily": {"required_fields": ("symbol", "trading_date", "close")}},
        )
        self.assertEqual(plan.strategy, "countertrend_rebound_shadow")
        self.assertEqual(plan.purpose, "replay")
        self.assertEqual(plan.required_capabilities[0], "bars.daily")
        request = plan.reads[0].request
        self.assertIsInstance(request, CapabilityRequest)
        self.assertEqual(request.purpose, "replay")
        self.assertEqual(request.required_fields, ("symbol", "trading_date", "close"))
        self.assertNotIn("tushare", repr(plan).lower())

    async def test_resolve_preserves_quality_and_provenance(self):
        plan = compile_strategy_data_plan("post_close_base_candidates")
        calls = []

        async def loader(request):
            calls.append(request)
            return StrategyCapabilityValue(
                [{"symbol": "000001.SZ"}],
                provenance={"capability": request.capability, "source": "persisted-evidence"},
            )

        context = await resolve_strategy_data(plan, loader)
        self.assertEqual(len(calls), len(plan.reads))
        self.assertEqual(context.value("bars.daily"), [{"symbol": "000001.SZ"}])
        self.assertEqual(context.provenance["bars.daily"]["source"], "persisted-evidence")
        self.assertEqual(context.optional_missing, ())

    async def test_required_failure_is_fail_closed(self):
        plan = compile_strategy_data_plan("limit_up_continuation")

        async def loader(_request):
            raise RuntimeError("source unavailable")

        with self.assertRaises(StrategyDataUnavailable) as raised:
            await resolve_strategy_data(plan, loader)
        self.assertEqual(raised.exception.strategy, "limit_up_continuation")
        self.assertEqual(raised.exception.capability, "bars.daily")

    async def test_required_quality_failure_is_fail_closed(self):
        plan = compile_strategy_data_plan("post_close_base_candidates", purpose="replay")
        receipt = QualityReceipt(
            status="partial", row_count=1, coverage=0.5,
            effective_at_min=None, effective_at_max=None,
            available_at_min=None, available_at_max=None,
            response_hash="hash",
        )

        async def loader(_request):
            return StrategyCapabilityValue([{"symbol": "000001.SZ"}], quality=receipt)

        with self.assertRaises(StrategyDataUnavailable) as raised:
            await resolve_strategy_data(plan, loader)
        self.assertEqual(raised.exception.capability, "bars.daily")

    async def test_optional_bad_quality_is_recorded_as_missing(self):
        plan = compile_strategy_data_plan("intraday_watchlist_confirmation")
        receipt = QualityReceipt(
            status="stale", row_count=1, coverage=1.0,
            effective_at_min=None, effective_at_max=None,
            available_at_min=None, available_at_max=None,
            response_hash="hash",
        )

        async def loader(request):
            if request.capability == "quote.order_book":
                return StrategyCapabilityValue([{"symbol": "000001.SZ"}], quality=receipt)
            return [{"ok": True}]

        context = await resolve_strategy_data(plan, loader)
        self.assertEqual(context.optional_missing, ("quote.order_book",))

    async def test_optional_failure_does_not_hide_required_inputs(self):
        plan = compile_strategy_data_plan("intraday_watchlist_confirmation")

        async def loader(request):
            if request.capability == "quote.order_book":
                raise RuntimeError("optional source unavailable")
            return [{"ok": True}]

        context = await resolve_strategy_data(plan, loader)
        self.assertEqual(context.optional_missing, ("quote.order_book",))
        self.assertIn("quote.watch_snapshot", context.values)

    def test_unknown_strategy_fails_before_any_loader(self):
        with self.assertRaisesRegex(ValueError, "unknown strategy data needs"):
            compile_strategy_data_plan("not-a-registered-strategy")


if __name__ == "__main__":
    unittest.main()
