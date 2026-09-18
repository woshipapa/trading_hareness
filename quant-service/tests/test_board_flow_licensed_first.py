import asyncio
import unittest
from types import SimpleNamespace

from app.board_flow_capture_actions import BoardFlowCaptureActions

LICENSED = [{"taxonomy_key": "longhu_ths_industry", "sector_key": "881121",
             "label": "半导体", "net_inflow": 1.0, "change_pct": 4.9}]
EASTMONEY_ROWS = [{"行业": "半导体", "行业代码": "BK1036", "净额": 2.0, "行业-涨跌幅": 4.8}]


class _Connection:
    def execute(self, *_args):
        return SimpleNamespace(fetchone=lambda: None, fetchall=lambda: [], rowcount=0)


class _Database:
    def transaction(self):
        class _Ctx:
            def __enter__(self_inner):
                return _Connection()

            def __exit__(self_inner, *_args):
                return False

        return _Ctx()


class BoardFlowLicensedFirstTests(unittest.TestCase):
    """Industry flow is a paid contract; the public feed is the fallback."""

    def _capture(self, *, licensed, akshare_kinds=None):
        seen = {"akshare": [], "persisted": None}

        async def run_database(action, *args, **kwargs):
            result = action(*args) if args else action()
            if getattr(action, "__name__", "") == "persist_snapshot":
                seen["persisted"] = True
            return result if result is not None else {}

        async def run_akshare(_fn, kind, **_kwargs):
            seen["akshare"].append(kind)
            if akshare_kinds is not None and kind not in akshare_kinds:
                raise RuntimeError("public feed unavailable")
            return list(EASTMONEY_ROWS)

        async def provider_capabilities(_provider, _capabilities):
            return set()

        async def retry_rotation_deliveries():
            return {"retried": 0}

        licensed_flow = None
        if licensed is not None:
            async def licensed_flow():  # noqa: F811
                if isinstance(licensed, Exception):
                    raise licensed
                return list(licensed)

        result = asyncio.run(BoardFlowCaptureActions(_Database()).capture(
            run_database=run_database, run_akshare=run_akshare,
            provider_capabilities=provider_capabilities,
            normalize_items=lambda kind, rows: [
                {"taxonomy_key": f"eastmoney_{kind}", "sector_key": "BK1036",
                 "label": "半导体", "net_inflow": 2.0, "change_pct": 4.8}],
            persist_feature=lambda *_args, **_kwargs: {},
            evaluate_rotation=lambda *_args: [],
            retry_rotation_deliveries=retry_rotation_deliveries,
            licensed_industry_flow=licensed_flow,
        ))
        return result, seen

    def test_a_healthy_licensed_answer_costs_no_public_industry_call(self):
        result, seen = self._capture(licensed=LICENSED)
        self.assertEqual(seen["akshare"], ["concept"])
        self.assertEqual(result["status"], "completed")

    def test_the_licensed_rows_are_the_ones_stored_for_industry(self):
        result, _seen = self._capture(licensed=LICENSED)
        self.assertEqual(result["providers"]["industry"], "longhuvip")
        self.assertEqual(result["coverage"]["industry"]["flow_boards"], 1)
        self.assertEqual(result["source_status"]["industry"]["provider"], "longhuvip")

    def test_both_kinds_are_stored_without_folding_their_vendors_together(self):
        # Two vendors do not agree on what a board is; one series carrying both
        # keys would read as a rotation that only crossed providers.
        result, _seen = self._capture(licensed=LICENSED)
        self.assertEqual(result["items"], 2)
        self.assertEqual(result["providers"],
                         {"concept": "eastmoney_free", "industry": "longhuvip"})

    def test_a_failing_licensed_call_falls_back_to_the_public_feed(self):
        result, seen = self._capture(licensed=RuntimeError("gateway down"))
        self.assertIn("industry", seen["akshare"])
        self.assertEqual(result["providers"]["industry"], "eastmoney_free")
        self.assertEqual(result["source_status"]["industry_licensed"]["status"], "failed")

    def test_an_empty_licensed_answer_falls_back_rather_than_storing_nothing(self):
        result, seen = self._capture(licensed=[])
        self.assertIn("industry", seen["akshare"])
        self.assertEqual(result["source_status"]["industry_licensed"]["status"], "empty")

    def test_without_a_licensed_source_the_behaviour_is_unchanged(self):
        result, seen = self._capture(licensed=None)
        self.assertEqual(sorted(seen["akshare"]), ["concept", "industry"])
        self.assertNotIn("industry_licensed", result["source_status"])
        self.assertEqual(result["providers"]["industry"], "eastmoney_free")


if __name__ == "__main__":
    unittest.main()
