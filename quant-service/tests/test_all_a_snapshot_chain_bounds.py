"""The fallback chain under an outage: a failed fill remembered, one bound on the leg, an unusable snapshot.

From the review of the TDX fallback (docs/work/probes/2026-10-14-tdx-fallback-review.md, findings
3, 4 and 6).
"""

from __future__ import annotations

import asyncio
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from app import all_a_snapshot_sources as sources
from app.all_a_snapshot_sources import AllASnapshotFallbackError, FailedFillMemory
from app.datasources.contracts import CapabilityEvidence
from app.fuyao_provider import FuyaoProviderError
from app.public_provider_rate_limits import PublicProviderRateLimited

import test_all_a_snapshot_sources as chain_tests

OBSERVED = datetime(2026, 10, 13, 2, 0, tzinfo=timezone.utc)
FUYAO_ANSWER = ([{"symbol": "000001.SZ", "price": 10.0}], {"provider_key": "fuyao_ths", "decision_eligible": True})


def fuyao_down():
    return FuyaoProviderError("fuyao down", code=5001, request_id="req-1")


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class FailedFillMemoryTests(unittest.TestCase):
    def chain(self, tdx, roster=5580):
        clock = Clock()
        return chain_tests.Calls(fuyao=fuyao_down(), tdx=tdx, roster=roster), FailedFillMemory(clock=clock), clock

    @staticmethod
    def read(calls, memory):
        return asyncio.run(sources.read(calls.fuyao, calls.tdx, calls.roster, memory))

    def test_the_memory_is_as_long_as_a_successful_snapshot_is_reused(self):
        from app import main

        self.assertEqual(sources.TDX_FAILED_FILL_MEMORY_SECONDS, main.INTRADAY_ALL_A_SNAPSHOT_TTL_SECONDS)
        self.assertEqual(sources.TDX_FAILED_FILL_MEMORY_SECONDS, 30.0)

    def test_a_failed_fill_is_raised_again_without_a_new_tdx_read_until_its_time_has_passed(self):
        # 09:15-09:25: the main source is down and no TDX row is priced.
        calls, memory, clock = self.chain(chain_tests.tdx_evidence(0, 5579))
        with self.assertRaises(AllASnapshotFallbackError) as first:
            self.read(calls, memory)
        self.assertTrue(first.exception.fallback_reason.startswith("tdx_snapshot_incomplete:"))
        for seconds in (5.0, 29.9):
            clock.now = 1000.0 + seconds
            with self.assertRaises(AllASnapshotFallbackError) as caught:
                self.read(calls, memory)
            error = caught.exception
            self.assertIs(error.__cause__, calls.fuyao_result)
            self.assertEqual((error.code, error.request_id), (5001, "req-1"))
            self.assertEqual(error.fallback_reason,
                             sources.FAILED_FILL_REMEMBERED_PREFIX + first.exception.fallback_reason)
        # The main source was tried every time; TDX and the roster once.
        self.assertEqual((calls.fuyao_calls, calls.tdx_calls, calls.roster_calls), (3, 1, 1))
        clock.now = 1030.0
        with self.assertRaises(AllASnapshotFallbackError) as later:
            self.read(calls, memory)
        self.assertEqual(later.exception.fallback_reason, first.exception.fallback_reason)
        self.assertEqual((calls.fuyao_calls, calls.tdx_calls), (4, 2))
        # The new failure is remembered from its own time.
        clock.now = 1059.9
        with self.assertRaises(AllASnapshotFallbackError):
            self.read(calls, memory)
        self.assertEqual(calls.tdx_calls, 2)

    def test_a_tdx_error_and_a_timeout_are_remembered(self):
        calls, memory, _clock = self.chain(RuntimeError("no TDX host answered"))
        for _ in range(3):
            with self.assertRaises(AllASnapshotFallbackError):
                self.read(calls, memory)
        self.assertEqual(calls.tdx_calls, 1)

        reads = []

        async def scenario():
            release = asyncio.Event()

            async def never_answers():
                reads.append("read")
                await release.wait()

            memory = FailedFillMemory(clock=Clock())
            reasons = []
            for _ in range(2):
                with self.assertRaises(AllASnapshotFallbackError) as caught:
                    await sources.read(calls.fuyao, never_answers, calls.roster, memory)
                reasons.append(caught.exception.fallback_reason)
            release.set()
            await asyncio.sleep(0)
            return reasons

        with patch.object(sources, "TDX_READ_TIMEOUT_SECONDS", 0.02):
            reasons = asyncio.run(scenario())
        self.assertEqual(reasons, ["tdx_read_timeout:0.02s", "tdx_fill_failed_recently:tdx_read_timeout:0.02s"])
        self.assertEqual(reads, ["read"])

    def test_a_refused_slot_is_not_remembered(self):
        calls, memory, _clock = self.chain(PublicProviderRateLimited("tdx_public"))
        with self.assertRaises(AllASnapshotFallbackError):
            self.read(calls, memory)
        calls.tdx_result = chain_tests.tdx_evidence()
        _rows, metadata = self.read(calls, memory)
        self.assertEqual(metadata["provider_key"], "tdx_public")
        self.assertEqual(calls.tdx_calls, 2)

    def test_a_fill_is_not_remembered_and_forgets_nothing_it_should_keep(self):
        calls, memory, _clock = self.chain(chain_tests.tdx_evidence())
        for expected_reads in (1, 2):
            _rows, metadata = self.read(calls, memory)
            self.assertEqual(metadata["provider_key"], "tdx_public")
            self.assertEqual(calls.tdx_calls, expected_reads)
        self.assertIsNone(memory.recent())

    def test_the_main_source_answering_is_returned_whatever_is_remembered(self):
        calls, memory, _clock = self.chain(chain_tests.tdx_evidence(0, 5579))
        with self.assertRaises(AllASnapshotFallbackError):
            self.read(calls, memory)
        answer = FUYAO_ANSWER
        calls.fuyao_result = answer
        self.assertIs(self.read(calls, memory), answer)
        self.assertEqual(calls.tdx_calls, 1)

    def test_the_chain_without_a_memory_reads_every_time_as_before(self):
        calls = chain_tests.Calls(fuyao=fuyao_down(), tdx=chain_tests.tdx_evidence(0, 5579))
        for _ in range(2):
            with self.assertRaises(AllASnapshotFallbackError):
                calls.read()
        self.assertEqual(calls.tdx_calls, 2)


class FallbackLegBoundTests(unittest.TestCase):
    def test_the_bound_on_the_fallback_leg_is_twenty_seconds(self):
        self.assertEqual(sources.TDX_READ_TIMEOUT_SECONDS, 20.0)

    def test_a_roster_read_that_outlives_the_bound_fails_closed_inside_it(self):
        started = []

        async def fuyao():
            raise fuyao_down()

        async def tdx():
            await asyncio.sleep(0.03)
            return chain_tests.tdx_evidence()

        async def slow_roster():
            started.append("roster")
            await asyncio.sleep(30)
            return 5580

        async def scenario():
            loop = asyncio.get_running_loop()
            began = loop.time()
            with self.assertRaises(AllASnapshotFallbackError) as caught:
                await sources.read(fuyao, tdx, slow_roster)
            return caught.exception.fallback_reason, loop.time() - began

        with patch.object(sources, "TDX_READ_TIMEOUT_SECONDS", 0.1):
            reason, waited = asyncio.run(scenario())
        self.assertEqual(started, ["roster"])
        self.assertIn("tdx_snapshot_incomplete:", reason)
        self.assertIn("roster_total=None", reason)
        # One bound for the read and the roster together, not one each.
        self.assertLess(waited, 0.19)

    def test_a_roster_read_inside_the_bound_fills(self):
        async def fuyao():
            raise fuyao_down()

        async def tdx():
            return chain_tests.tdx_evidence()

        async def roster():
            await asyncio.sleep(0.01)
            return 5580

        with patch.object(sources, "TDX_READ_TIMEOUT_SECONDS", 5.0):
            _rows, metadata = asyncio.run(sources.read(fuyao, tdx, roster))
        self.assertEqual(metadata["completeness"]["roster_total"], 5580)


class UnprojectableSnapshotTests(unittest.TestCase):
    def test_a_malformed_tdx_snapshot_leaves_the_main_failure_standing(self):
        for label, evidence in (
            ("missing key", CapabilityEvidence(rows=[{"symbol": "000001.SZ", "price": 11.0}] * 5535,
                                               available_at_max=OBSERVED, warnings=())),
            ("price not a number", CapabilityEvidence(rows=[{**chain_tests.TDX_ROW, "price": "11.73"}] * 5535,
                                                      available_at_max=OBSERVED, warnings=())),
            ("read time not a time", CapabilityEvidence(rows=[chain_tests.TDX_ROW] * 5535,
                                                        available_at_max="2026-10-13", warnings=())),
            ("warning not a count", CapabilityEvidence(rows=[chain_tests.TDX_ROW] * 5535,
                                                       available_at_max=OBSERVED, warnings=("no_trade_rows=many",))),
        ):
            with self.subTest(label):
                calls = chain_tests.Calls(fuyao=fuyao_down(), tdx=evidence)
                memory = FailedFillMemory(clock=Clock())
                with self.assertRaises(AllASnapshotFallbackError) as caught:
                    asyncio.run(sources.read(calls.fuyao, calls.tdx, calls.roster, memory))
                self.assertIs(caught.exception.__cause__, calls.fuyao_result)
                self.assertTrue(caught.exception.fallback_reason.startswith("tdx_projection_failed:"))
                self.assertIsNotNone(memory.recent())

if __name__ == "__main__":
    unittest.main()
