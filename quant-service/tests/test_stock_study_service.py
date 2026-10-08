from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import date
from types import SimpleNamespace
import unittest

from app.stock_study_service import StockStudyDependencies, build

LOCAL_BARS = [{"trade_date": "20260820", "close": 9.8}, {"trade_date": "20260821", "close": 10.0}]


class StockStudyServiceTests(unittest.TestCase):
    @staticmethod
    def dependencies(*, summarized: list[object], local_bars=LOCAL_BARS) -> StockStudyDependencies:
        async def free_fetch(label, provider, capability, fetcher, _symbol):
            payload = await fetcher()
            return ({"source": label, "api_name": capability, "provider": provider, "status": "completed",
                     "received": len(payload) if isinstance(payload, list) else int(bool(payload)), "stored": 1}, payload)

        async def run_database(action, *args, **_kwargs):
            return action(*args)

        async def baostock(_request):
            return {"status": "completed", "imported": 1, "failures": []}

        async def daily(*_args):
            return [{"trade_date": "20260821", "close": 10.0}]

        async def quote(*_args):
            return {"price": 10.0}

        async def run_akshare(action, *args, **_kwargs):
            return action(*args)

        def akshare_daily(*_args):
            return [{"trade_date": "20260821", "close": 10.0}]

        async def announcements(*_args, **_kwargs):
            return [{"ts_code": "000001.SZ", "title": "公告"}]

        async def read_daily_bars(_symbol, _start, _end):
            return list(local_bars)

        def summary(rows):
            summarized.append(rows)
            return {"score": 70, "reasons": ["trend"]}

        return StockStudyDependencies(
            china_today=lambda: date(2026, 8, 21), daily_sync_request=lambda **kwargs: SimpleNamespace(**kwargs),
            sync_baostock=baostock,
            free_fetch=free_fetch, eastmoney_daily=daily, eastmoney_quote=quote, run_akshare=run_akshare,
            akshare_daily=akshare_daily, tencent_daily=daily, sina_quote=quote,
            cninfo_announcements=announcements, run_database=run_database,
            persist_market_events=lambda _provider, rows: len(rows), persist_announcement_health=lambda *_args: None,
            technical_summary=summary,
            analyst_claims=lambda _symbol: ([{"id": 1}], {"score": 0.4, "claim_count": 1, "direction": "positive"}),
            recent_events=lambda _symbol, _limit: [{"title": "公告"}],
            window_readiness=lambda _symbol, _start, _end: {"decision_ready": False, "blockers": ["daily_basic"]},
            latest_row=lambda rows: rows[-1] if rows else None,
            read_daily_bars=read_daily_bars,
        )

    def test_the_technical_summary_reads_the_local_close_history(self) -> None:
        summarized: list[object] = []
        request = SimpleNamespace(as_of_date=date(2026, 8, 22), lookback_days=21)

        result = asyncio.run(build("000001.SZ", request, self.dependencies(summarized=summarized)))

        self.assertEqual(result["as_of_date"], "2026-08-21")
        self.assertEqual(summarized, [LOCAL_BARS])
        self.assertEqual(result["market"]["daily_bars"], LOCAL_BARS)
        local = next(item for item in result["sources"] if item["provider"] == "canonical_bars_daily")
        self.assertEqual((local["status"], local["received"]), ("completed", 2))
        self.assertIsNone(result["market"]["latest_realtime"])
        self.assertEqual(result["market"]["tencent_daily_bars"][0]["close"], 10.0)
        self.assertEqual(result["events"]["decision_eligible"], False)
        self.assertIn("不构成交易指令", result["combined"]["notice"])

    def test_no_local_history_is_reported_not_hidden(self) -> None:
        summarized: list[object] = []
        result = asyncio.run(build("000001.SZ", SimpleNamespace(as_of_date=date(2026, 8, 22), lookback_days=21),
                                   self.dependencies(summarized=summarized, local_bars=[])))
        local = next(item for item in result["sources"] if item["provider"] == "canonical_bars_daily")
        self.assertEqual(local["status"], "missing")
        self.assertEqual(summarized, [[]])

    def test_owner_factor_reader_supplies_the_adjustment_factor(self) -> None:
        deps = self.dependencies(summarized=[])

        async def persisted(_symbol, _start, _end):
            return [{"trading_date": date(2026, 8, 21), "adj_factor": 1.2,
                     "factor_provider": "longhu_qfq_derived"}]

        deps = replace(deps, read_persisted_factors=persisted)
        result = asyncio.run(build(
            "000001.SZ", SimpleNamespace(as_of_date=date(2026, 8, 22), lookback_days=21), deps,
        ))
        self.assertEqual(result["market"]["latest_adj_factor"]["factor_provider"], "longhu_qfq_derived")
        factor_sources = [item for item in result["sources"] if item["api_name"] == "adj_factor"]
        self.assertEqual(factor_sources[0]["provider"], "owner_persisted_adjustment_factor")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
