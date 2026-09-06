from datetime import date
import unittest

from app.longhu_supplemental_service import BASE_REQUESTS, supplemental_requests, sync


class LonghuSupplementalServiceTests(unittest.TestCase):
    def test_supplemental_sync_persists_each_page_and_degrades_per_capability(self):
        calls = []
        stored = []

        class Source:
            def raw_call(self, request):
                calls.append(request)
                if request["params"]["a"] == "MoodNumCount":
                    raise RuntimeError("provider unavailable")
                return {"pages": [{"payload": {"errcode": 0, "list": [["600000", 1]]}}]}

        async def run_public(fn, request, **_kwargs):
            return fn(request)

        async def persist(provider, capability, rows):
            stored.append((provider, capability, rows))
            return len(rows)

        import asyncio
        result = asyncio.run(sync(date(2026, 9, 4), run_public_blocking=run_public, persist=persist, source_factory=Source))
        expected = supplemental_requests(date(2026, 9, 4))
        self.assertEqual(len(calls), len(expected))
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["capabilities"]["longhu:longhu_market_wide:MoodNumCount"]["status"], "failed")
        self.assertEqual(result["stored"], len(expected) - 1)
        self.assertTrue(all(capability.startswith("longhu:") for _provider, capability, _rows in stored))
        self.assertEqual(len({capability for _provider, capability, _rows in stored}), len(expected) - 1)


    def test_supplemental_requests_include_all_limit_buckets_and_next_session_boundaries(self):
        requests = supplemental_requests(date(2026, 9, 4))
        history_limits = [item for item in requests if item["target"] == "longhu_history" and item["action"].startswith("DailyLimitPerformance")]
        self.assertEqual(len(history_limits), 10)
        self.assertEqual({item["params"]["PidType"] for item in history_limits}, {1, 2, 3, 4, 5})
        auction = next(item for item in requests if item["action"] == "MorningBiddingList")
        self.assertEqual(auction["params"]["Date"], "2026-09-04")
        self.assertTrue(auction["next_session_only"])
        self.assertEqual(len(BASE_REQUESTS), 4)
