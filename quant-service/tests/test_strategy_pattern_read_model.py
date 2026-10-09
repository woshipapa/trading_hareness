"""The pattern-mining read models show the Fuyao close pool and ladder, not Tushare rows."""

from __future__ import annotations

import json
import unittest
from contextlib import asynccontextmanager, contextmanager
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from app.async_strategy_pattern_read_repository import latest_strategy_pattern_mining as read_async
from app.limit_pool_merge import merge_limit_pool_sources
from app.post_close_limit_features import board_count
from app.strategy_pattern_read_model import latest_strategy_pattern_mining as read_sync

CN = ZoneInfo("Asia/Shanghai")
DAY = date(2026, 9, 18)
CLOSE = datetime(2026, 9, 18, 14, 59, 31, tzinfo=CN)
RUN = {"run_id": "run-1", "run_key": "k", "as_of_date": DAY, "model_version": "post-close-limit-lift-pattern-v4",
       "status": "completed", "source_status": {}, "summary": {}, "created_at": CLOSE, "updated_at": CLOSE}


def fuyao(symbol: str, name: str, capability: str, observed: datetime = CLOSE, **fields) -> dict:
    body = {"capability": capability, "thscode": symbol, "ticker": symbol[:6], "name": name, **fields}
    return {"symbol": symbol, "body": json.dumps(body, ensure_ascii=False), "source": "fuyao_ths",
            "occurred_at": observed, "available_at": observed}


POOL = [
    fuyao("603721.SH", "中广天择", "a_share_limit_up_pool", last_price=21.01, price_change_ratio_pct=10,
          limit_up_time="09:25", limit_up_reason="AI语料+传媒内容+控股变更", continue_day_text="2连板",
          continue_day_cnt=2, seal_money=114685186, max_seal_money=241522556.0),
    fuyao("000596.SZ", "古井贡酒", "a_share_limit_up_pool", last_price=100.88, price_change_ratio_pct=9.9989,
          limit_up_time="10:21", limit_up_reason="白酒+渠道去库+国资背景", continue_day_text="首板",
          continue_day_cnt=1, seal_money=508882910, max_seal_money=1043906240.0),
]
LADDER = [fuyao("603721.SH", "中广天择", "a_share_limit_up_ladder", CLOSE - timedelta(hours=5),
                bucket="two_board", board_num=2, seal_nextday=None, sign_level=0)]
SETTLED = [{"symbol": "000001.SZ", "event_type": "limit_up_pool", "source": "longhuvip_composite_close_limit_derived",
            "body": json.dumps({"ts_code": "000001.SZ", "name": "平安银行", "status": "收盘封板", "turnover_rate": 3.2}),
            "occurred_at": CLOSE + timedelta(hours=1), "available_at": CLOSE + timedelta(hours=1)}]


def route(sql: str) -> str:
    if "strategy_pattern_runs" in sql:
        return "run"
    if "strategy_pattern_samples" in sql:
        return "samples"
    if "canonical_bars_daily" in sql:
        return "daily"
    if "event_type='limit_chain'" in sql:
        return "ladder"
    if "source<>%s" in sql:
        return "others"
    if "event_type='limit_up_pool'" in sql:
        return "pool"
    raise AssertionError(f"unexpected read: {sql[:80]}")


ROWS = {"run": [RUN], "samples": [], "daily": [], "ladder": LADDER, "others": SETTLED, "pool": POOL}


class Result:
    def __init__(self, rows):
        self.rows = rows

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows


class Database:
    def __init__(self):
        self.reads: list[str] = []
        self.sql: list[str] = []

    @contextmanager
    def transaction(self):
        yield self

    def execute(self, sql, params=None):
        name = route(str(sql))
        self.reads.append(name)
        self.sql.append(str(sql))
        return Result(ROWS[name])


class AsyncDatabase(Database):
    @asynccontextmanager
    async def transaction(self):  # type: ignore[override]
        yield self

    async def execute(self, sql, params=None):  # type: ignore[override]
        result = Database.execute(self, sql, params)

        class Cursor:
            async def fetchone(self_inner):
                return result.fetchone()

            async def fetchall(self_inner):
                return result.fetchall()
        return Cursor()


def merge(primary, secondary):
    return merge_limit_pool_sources(primary, secondary,
                                    json_safe=lambda value: json.loads(json.dumps(value, ensure_ascii=False, default=str)),
                                    number=lambda value: float(value) if value not in (None, "") else None)


DEPENDENCIES = (merge, board_count, lambda value: json.loads(json.dumps(value, ensure_ascii=False, default=str)),
                lambda _rows: {"status": "missing_daily_bar"}, lambda _day: {}, lambda _day: {})


class StrategyPatternReadModelTests(unittest.IsolatedAsyncioTestCase):
    def assert_payload(self, payload, database) -> None:
        self.assertNotIn("tushare_raw_records", " ".join(database.sql))
        self.assertEqual(database.reads[:5], ["run", "samples", "pool", "ladder", "others"])
        by_symbol = {item["ts_code"]: item for item in payload["limit_pool"]}
        self.assertEqual(set(by_symbol), {"603721.SH", "000596.SZ", "000001.SZ"})
        relay = by_symbol["603721.SH"]
        self.assertEqual(relay["sources"], ["market_events:fuyao_ths"])
        self.assertEqual((relay["tag"], relay["board_count"], relay["status"]), ("2连板", 2, "涨停"))
        self.assertEqual(relay["limit_amount"], 114685186.0)
        self.assertEqual(relay["lu_desc"], "AI语料+传媒内容+控股变更")
        self.assertIsNone(relay["turnover_rate"])
        self.assertEqual(relay["continuation_watch"]["status"], "unavailable")
        self.assertEqual(by_symbol["000001.SZ"]["sources"], ["market_events:longhuvip_composite_close_limit_derived"])
        self.assertEqual(payload["continuation_candidates"], [])
        ladder = payload["limit_ladder"]
        self.assertEqual([(row["ts_code"], row["nums"]) for row in ladder], [("603721.SH", 2)])
        self.assertEqual(ladder[0]["ladder_sources"], ["limit_up_pool_tag", "limit_chain"])
        self.assertEqual(ladder[0]["tag"], "2连板")
        coverage = payload["pool_coverage"]
        self.assertEqual(coverage["status"], "two_source_union")
        self.assertEqual((coverage["tushare_count"], coverage["eastmoney_count"]), (2, 1))
        self.assertEqual(coverage["primary_sources"], ["market_events:fuyao_ths"])
        self.assertEqual((coverage["limit_step_count"], coverage["multi_board_union_count"]), (1, 1))
        self.assertEqual(coverage["close_snapshot"]["status"], "completed")
        self.assertEqual(coverage["close_snapshot"]["observed_at"], CLOSE.isoformat())

    def test_sync_read_model_projects_the_fuyao_close_pool(self) -> None:
        database = Database()
        self.assert_payload(read_sync(database, *DEPENDENCIES), database)

    async def test_async_read_model_projects_the_same_close_pool(self) -> None:
        database = AsyncDatabase()

        async def runner(fn, *args, **_kwargs):
            return fn(*args)

        payload = await read_async(database, *DEPENDENCIES, database_runner=runner)
        self.assert_payload(payload, database)


if __name__ == "__main__":
    unittest.main()
