from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import date, datetime, timedelta
import unittest
from zoneinfo import ZoneInfo

from app.limit_event_repository import session_window
from app.strategy_pattern_sample_repository import (
    load_strategy_pattern_sample_inputs,
    persist_strategy_pattern_run,
)

CN = ZoneInfo("Asia/Shanghai")


def fuyao_event(symbol: str, name: str, observed: datetime, capability: str, **fields) -> dict:
    """A Fuyao pool or ladder item as ``market_event_capture`` stores it."""
    body = {"capability": capability, "thscode": symbol, "ticker": symbol[:6], "name": name, **fields}
    return {"symbol": symbol, "body": json.dumps(body, ensure_ascii=False), "source": "fuyao_ths",
            "occurred_at": observed, "available_at": observed}


def settled(symbol: str, observed: datetime, **fields) -> dict:
    """A close-at-limit row of ``settled_limit_pool_repository`` (another source)."""
    body = {"ts_code": symbol, "trade_date": observed.strftime("%Y%m%d"), "limit_type": "涨停池",
            "status": "收盘封板", "limit_amount": None, **fields}
    return {"symbol": symbol, "body": json.dumps(body, ensure_ascii=False),
            "source": "longhuvip_composite_close_limit_derived", "event_type": "limit_up_pool",
            "occurred_at": observed, "available_at": observed}


class Result:
    def __init__(self, *, rows=None, row=None):
        self.rows, self.row = rows or [], row

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return self.row


class Database:
    def __init__(self, results):
        self.calls = []
        self.results = iter(results)

    @contextmanager
    def transaction(self):
        yield self

    def execute(self, sql, params=None):
        self.calls.append((str(sql), params))
        return next(self.results)


class StrategyPatternSampleRepositoryTests(unittest.TestCase):
    def test_positives_are_the_fuyao_close_pool_with_its_ladder(self) -> None:
        close = datetime(2026, 8, 17, 14, 59, 40, tzinfo=CN)
        prior_close = datetime(2026, 8, 14, 15, 0, 12, tzinfo=CN)
        pool = dict(last_price=24.85, price_change_ratio_pct=10.0044, limit_up_time="09:30",
                    limit_up_reason="AI短剧+短剧出海+数字阅读", continue_day_text="2连板", continue_day_cnt=2,
                    seal_money=94507035, max_seal_money=216515565.0)
        database = Database([
            Result(rows=[fuyao_event("603533.SH", "掌阅科技", close, "a_share_limit_up_pool", **pool)]),
            Result(rows=[fuyao_event("603533.SH", "掌阅科技", close - timedelta(hours=5),
                                     "a_share_limit_up_ladder", bucket="two_board", board_num=2)]),
            # The settled close-at-limit rows: one fills turnover, one is not a Fuyao positive.
            Result(rows=[settled("603533.SH", close + timedelta(hours=1), turnover_rate=8.4),
                         settled("000001.SZ", close + timedelta(hours=1), turnover_rate=3.2)]),
            Result(rows=[fuyao_event("603533.SH", "掌阅科技", prior_close, "a_share_limit_up_pool",
                                     continue_day_text="首板", continue_day_cnt=1, seal_money=5_000_000)]),
            Result(rows=[]),
            Result(rows=[{"symbol": "603533.SH", "trading_date": date(2026, 8, 17), "close": 24.85}]),
        ])
        inputs = load_strategy_pattern_sample_inputs(database, date(2026, 8, 17))
        self.assertEqual(len(database.calls), 6)
        self.assertEqual([row["row_data"]["ts_code"] for row in inputs.limit_rows], ["603533.SH"])
        positive = inputs.limit_rows[0]
        self.assertEqual(positive["provider_key"], "market_events:fuyao_ths")
        self.assertEqual(positive["row_data"]["tag"], "2连板")
        self.assertEqual(positive["row_data"]["limit_amount"], 94507035.0)
        self.assertEqual(positive["row_data"]["lu_desc"], "AI短剧+短剧出海+数字阅读")
        self.assertEqual(positive["row_data"]["turnover_rate"], 8.4)
        self.assertEqual(positive["row_data"]["enriched_from"], {
            "provider_key": "market_events:longhuvip_composite_close_limit_derived", "fields": ["turnover_rate"]})
        self.assertIsNone(positive["row_data"]["open_num"])
        self.assertEqual([(row["ts_code"], row["nums"]) for row in inputs.step_rows], [("603533.SH", 2)])
        self.assertEqual([(row["ts_code"], row["trade_date"]) for row in inputs.prior_limit_rows],
                         [("603533.SH", "20260814")])
        self.assertEqual(inputs.daily_rows[0]["close"], 24.85)
        start, end = session_window(date(2026, 8, 17))
        self.assertIn("quant.market_events", database.calls[0][0])
        self.assertNotIn("tushare_raw_records", " ".join(sql for sql, _params in database.calls))
        self.assertEqual(database.calls[0][1], ("fuyao_ths", start, end, "fuyao_ths"))
        self.assertEqual(database.calls[5][1], (["603533.SH"], date(2026, 8, 17), date(2026, 6, 18)))

    def test_capture_that_stopped_before_the_close_leaves_no_positive_and_skips_daily_query(self) -> None:
        intraday = datetime(2026, 8, 17, 13, 41, tzinfo=CN)
        database = Database([
            Result(rows=[fuyao_event("603533.SH", "掌阅科技", intraday, "a_share_limit_up_pool", seal_money=1)]),
            Result(rows=[]),
            Result(rows=[settled("603533.SH", intraday + timedelta(hours=3), turnover_rate=8.4)]),
            Result(rows=[]),
        ])
        inputs = load_strategy_pattern_sample_inputs(database, date(2026, 8, 17))
        self.assertEqual(inputs.limit_rows, [])
        self.assertEqual(inputs.step_rows, [])
        self.assertEqual(inputs.prior_limit_rows, [])
        self.assertEqual(inputs.daily_rows, [])
        self.assertEqual(len(database.calls), 4)

    def test_persist_replaces_one_bounded_run_without_fetching_or_reranking(self) -> None:
        class Result:
            def fetchone(self):
                return {"run_id": "run-1"}

        class Database:
            def __init__(self):
                self.calls = []

            @contextmanager
            def transaction(self):
                yield self

            def execute(self, sql, params=None):
                self.calls.append((str(sql), params))
                return Result()

        database = Database()
        run_id = persist_strategy_pattern_run(
            database, "key", date(2026, 8, 17), "completed", {"minute": "completed"}, {"selected": 1},
            [{
                "symbol": "000001.SZ", "primary_cohort": "limit_pool", "cohorts": ["limit_pool"],
                "board_context": {}, "limit_context": {}, "daily_features": {},
                "intraday_pattern": {"status": "completed"}, "risk_flags": [],
            }],
            model_version="test-v1", json_safe=lambda value: value,
        )

        self.assertEqual(run_id, "run-1")
        self.assertEqual(len(database.calls), 3)
        self.assertIn("INSERT INTO quant.strategy_pattern_runs", database.calls[0][0])
        self.assertIn("DELETE FROM quant.strategy_pattern_samples", database.calls[1][0])
        self.assertIn("INSERT INTO quant.strategy_pattern_samples", database.calls[2][0])


if __name__ == "__main__":
    unittest.main()
