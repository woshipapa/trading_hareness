"""The sentiment reading names the source of its tape (review of the TDX fallback, finding 2)."""

from __future__ import annotations

import asyncio
import unittest
from datetime import date
from unittest.mock import patch

from app.datasources.collectors import intraday, post_close

import test_datasource_collectors as collector_tests


class SentimentTapeLabelTests(unittest.TestCase):
    """The reading names the source of the tape it was built from, in the day-close record too."""

    def reading(self, metadata, *, close=False):
        recorder = collector_tests.Recorder()

        async def fuyao(_route, _params):
            return {"item": [], "pagination": {"pages": 1}}

        async def snapshot():
            return [{"symbol": "600000.SH", "pct_change": 10.0, "turnover": 2e8}], metadata

        async def previous_pool(_pool, _trade_date):
            return []

        async def none():
            return None

        deps = recorder.deps(fuyao_fetch=fuyao, fuyao_snapshot=snapshot)
        with patch("app.datasources.sources.eastmoney_ztb.fetch_pool", previous_pool):
            if not close:
                return asyncio.run(intraday.build_sentiment(deps, intraday.CollectorState(), collector_tests.SESSION))
            archive = post_close.ArchiveDeps(collector=deps, watch_symbols=none, previous_trading_day=none)
            asyncio.run(post_close.job_sentiment_close(
                archive, post_close.ArchiveState(), date(2026, 9, 18), collector_tests.SESSION))
        (_provider, capability, rows), = recorder.observations
        self.assertEqual(capability, "market_sentiment_close")
        return rows[0]

    def test_a_main_source_tape_is_labelled_exactly_as_before(self):
        expected = {"pools": "fuyao_ths", "tape": "fuyao_ths_all_a_snapshot", "previous_limit_up": "eastmoney_ztb",
                    "concept_quotes": None, "concept_membership": None}
        for metadata in ({}, None, {"status": "fresh", "source": "fuyao_ths_all_a_snapshot", "provider_key": "fuyao_ths",
                                    "decision_eligible": True}):
            for close in (False, True):
                with self.subTest(metadata=metadata, close=close):
                    sources_of = self.reading(metadata, close=close)["sources"]
                    self.assertEqual(sources_of, expected)
                    self.assertEqual(list(sources_of), list(expected))

    def test_a_filled_tape_is_labelled_with_its_own_source(self):
        metadata = {"status": "fresh", "source": "tdx_public_all_a_snapshot", "provider_key": "tdx_public",
                    "decision_eligible": False, "fallback_for": "fuyao_ths"}
        for close in (False, True):
            with self.subTest(close=close):
                sources_of = self.reading(metadata, close=close)["sources"]
                self.assertEqual(sources_of["tape"], "tdx_public_all_a_snapshot")
                self.assertEqual(sources_of["tape_fallback_for"], "fuyao_ths")
                self.assertEqual(sources_of["pools"], "fuyao_ths")

if __name__ == "__main__":
    unittest.main()
