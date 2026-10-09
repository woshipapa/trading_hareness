"""The Fuyao close-snapshot reads that replaced Tushare's limit pool and ladder."""

from __future__ import annotations

import json
import unittest
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from app.limit_event_repository import (
    CLOSE_SNAPSHOT_FROM, close_snapshot, limit_event_evidence, load_close_limit_events,
    load_close_limit_events_async, load_prior_close_pool, session_window,
)

CN = ZoneInfo("Asia/Shanghai")
DAY = date(2026, 9, 18)
CLOSE = datetime(2026, 9, 18, 14, 59, 31, tzinfo=CN)


def pool_event(symbol: str, name: str, observed: datetime, **fields) -> dict:
    """A ``limit_up_pool`` row exactly as ``market_event_capture`` stores Fuyao's item."""
    body = {"capability": "a_share_limit_up_pool", "thscode": symbol, "ticker": symbol[:6], "name": name,
            "is_st": False, "is_new": False, "last_price": 21.01, "price_change_ratio_pct": 10.0,
            "limit_up_time": "09:25", "limit_up_reason": "AI语料+传媒内容+控股变更",
            "continue_day_text": "首板", "continue_day_cnt": 1, "seal_money": 114685186, "max_seal_money": 241522556.0,
            **fields}
    return {"symbol": symbol, "body": json.dumps(body, ensure_ascii=False), "source": "fuyao_ths",
            "occurred_at": observed, "available_at": observed}


def chain_event(symbol: str, name: str, board_num: int, observed: datetime) -> dict:
    bucket = {2: "two_board", 3: "three_board"}.get(board_num, "first_board")
    body = {"capability": "a_share_limit_up_ladder", "bucket": bucket, "thscode": symbol, "ticker": symbol[:6],
            "name": name, "board_num": board_num, "seal_nextday": None, "sign_level": 0}
    return {"symbol": symbol, "body": json.dumps(body, ensure_ascii=False), "source": "fuyao_ths",
            "occurred_at": observed, "available_at": observed}


class Result:
    def __init__(self, rows):
        self.rows = rows

    def fetchall(self):
        return self.rows


class Connection:
    """Routes each query by its SQL so a test states the stored rows, not the call order."""

    def __init__(self, *, pool=(), ladder=(), others=(), prior=(), counts=()):
        self.routes = {"pool": list(pool), "ladder": list(ladder), "others": list(others),
                       "prior": list(prior), "counts": list(counts)}
        self.calls: list[tuple[str, tuple]] = []

    def route(self, sql: str, params: tuple) -> str:
        if "count(DISTINCT" in sql:
            return "counts"
        if "event_type='limit_chain'" in sql:
            return "ladder"
        if "source<>%s" in sql:
            return "others"
        # The same last-snapshot query serves the session and the previous one.
        return "prior" if params[2] <= session_window(DAY)[0] and params[1] < session_window(DAY)[0] else "pool"

    def execute(self, sql, params=None):
        name = self.route(str(sql), tuple(params or ()))
        self.calls.append((name, tuple(params or ())))
        return Result(self.routes[name])

    @contextmanager
    def transaction(self):
        yield self


class AsyncConnection(Connection):
    async def execute(self, sql, params=None):  # type: ignore[override]
        result = Connection.execute(self, sql, params)

        class Awaitable:
            async def fetchall(self_inner):
                return result.rows
        return Awaitable()


class CloseSnapshotTests(unittest.TestCase):
    def test_only_a_snapshot_from_the_closing_auction_is_the_close_pool(self) -> None:
        rows, snapshot = close_snapshot([pool_event("603721.SH", "中广天择", CLOSE)], "fuyao_ths")
        self.assertEqual(len(rows), 1)
        self.assertEqual(snapshot, {"status": "completed", "source": "fuyao_ths", "trade_date": "2026-09-18",
                                    "observed_at": CLOSE.isoformat(), "rows": 1})
        early = datetime.combine(DAY, CLOSE_SNAPSHOT_FROM, CN) - timedelta(minutes=80)
        rows, snapshot = close_snapshot([pool_event("603721.SH", "中广天择", early)], "fuyao_ths")
        self.assertEqual(rows, [])
        self.assertEqual(snapshot["status"], "unavailable")
        self.assertIn("13:37", snapshot["reason"])
        rows, snapshot = close_snapshot([], "fuyao_ths")
        self.assertEqual((rows, snapshot["status"], snapshot["rows"]), ([], "unavailable", 0))
        self.assertIn("no limit-up pool snapshot", snapshot["reason"])


class LoadCloseLimitEventsTests(unittest.IsolatedAsyncioTestCase):
    def connection(self, cls=Connection, observed=CLOSE):
        return cls(
            pool=[pool_event("603721.SH", "中广天择", observed, continue_day_text="2连板", continue_day_cnt=2),
                  pool_event("000596.SZ", "古井贡酒", observed)],
            # A rung is kept for the day even after the name breaks: 000560 left the pool.
            ladder=[chain_event("603721.SH", "中广天择", 2, CLOSE - timedelta(hours=5)),
                    chain_event("000560.SZ", "我爱我家", 3, CLOSE - timedelta(hours=5))],
            others=[{"symbol": "000001.SZ", "event_type": "limit_up_pool",
                     "source": "longhuvip_composite_close_limit_derived", "body": "{}",
                     "occurred_at": CLOSE, "available_at": CLOSE}],
        )

    def assert_projection(self, events) -> None:
        by_symbol = {item["row_data"]["ts_code"]: item for item in events.pool}
        relay = by_symbol["603721.SH"]
        self.assertEqual(relay["provider_key"], "market_events:fuyao_ths")
        self.assertEqual(relay["row_data"]["tag"], "2连板")
        self.assertEqual(relay["row_data"]["limit_amount"], 114685186.0)
        self.assertEqual(relay["row_data"]["max_seal_money"], 241522556.0)
        self.assertEqual(relay["row_data"]["trade_date"], "20260918")
        # Not in the Fuyao pool, so not invented.
        self.assertIsNone(relay["row_data"]["turnover_rate"])
        self.assertIsNone(relay["row_data"]["open_num"])
        self.assertEqual([item["row_data"]["ts_code"] for item in events.ladder], ["603721.SH"])
        self.assertEqual(events.ladder[0]["row_data"]["nums"], 2)
        self.assertEqual(events.others[0]["source"], "longhuvip_composite_close_limit_derived")
        self.assertEqual(events.snapshot["status"], "completed")
        self.assertEqual(events.snapshot["ladder_rows"], 1)

    def test_reads_the_session_window_with_catalog_sources(self) -> None:
        connection = self.connection()
        events = load_close_limit_events(connection, DAY)
        self.assert_projection(events)
        start, end = session_window(DAY)
        self.assertEqual(dict(connection.calls)["pool"], ("fuyao_ths", start, end, "fuyao_ths"))
        self.assertEqual(dict(connection.calls)["ladder"], ("fuyao_ths", start, end))
        self.assertEqual(dict(connection.calls)["others"], ("fuyao_ths", start, end))
        self.assertEqual(start.isoformat(), "2026-09-18T00:00:00+08:00")

    async def test_async_reader_shares_the_projection(self) -> None:
        connection = self.connection(AsyncConnection)
        self.assert_projection(await load_close_limit_events_async(connection, DAY))
        self.assertEqual([name for name, _params in connection.calls], ["pool", "ladder", "others"])

    def test_a_capture_that_stopped_before_the_close_yields_no_pool_or_ladder(self) -> None:
        connection = self.connection(observed=CLOSE - timedelta(hours=2))
        events = load_close_limit_events(connection, DAY)
        self.assertEqual((events.pool, events.ladder), ([], []))
        self.assertEqual(events.snapshot["status"], "unavailable")
        self.assertIn("12:59", events.snapshot["reason"])
        # Other sources are reported as they are; they never stand in for the close pool.
        self.assertEqual([row["symbol"] for row in events.others], ["000001.SZ"])

    def test_prior_session_is_the_last_close_snapshot_before_the_date(self) -> None:
        prior_close = datetime(2026, 9, 17, 15, 0, 5, tzinfo=CN)
        connection = Connection(prior=[pool_event("600127.SH", "金健米业", prior_close,
                                                  continue_day_text="3连板", continue_day_cnt=3)])
        rows = load_prior_close_pool(connection, DAY)
        self.assertEqual([(row["ts_code"], row["trade_date"], row["tag"]) for row in rows],
                         [("600127.SH", "20260917", "3连板")])
        _name, params = connection.calls[0]
        self.assertEqual(params[1:3], (session_window(DAY)[0] - timedelta(days=14), session_window(DAY)[0]))
        self.assertEqual(load_prior_close_pool(Connection(prior=[
            pool_event("600127.SH", "金健米业", prior_close - timedelta(hours=3))]), DAY), [])


class LimitEventEvidenceTests(unittest.TestCase):
    def test_reports_captured_evidence_and_requests_nothing(self) -> None:
        counts = [
            {"event_type": "limit_up_pool", "rows": 18_400, "symbols": 96, "snapshots": 236,
             "first_observed_at": datetime(2026, 9, 18, 9, 26, 3, tzinfo=CN), "last_observed_at": CLOSE},
            {"event_type": "limit_chain", "rows": 31, "symbols": 30, "snapshots": 12,
             "first_observed_at": datetime(2026, 9, 18, 9, 26, 3, tzinfo=CN), "last_observed_at": CLOSE},
        ]
        database = Connection(pool=[pool_event("603721.SH", "中广天择", CLOSE)], counts=counts)
        report = limit_event_evidence(database, DAY)
        self.assertEqual(report["status"], "completed")
        self.assertEqual(report["provider_requests"], 0)
        self.assertEqual(report["close_snapshot"]["rows"], 1)
        self.assertEqual(report["limit_up_pool"]["snapshots"], 236)
        self.assertEqual(report["limit_chain"]["symbols"], 30)
        self.assertEqual(report["concept_strength"]["status"], "unavailable")
        self.assertNotIn("reason", report)

    def test_without_a_close_snapshot_the_report_blocks(self) -> None:
        report = limit_event_evidence(Connection(), DAY)
        self.assertEqual(report["status"], "blocked")
        self.assertEqual(report["limit_up_pool"]["rows"], 0)
        self.assertIn("no limit-up pool snapshot", report["reason"])



class LimitEventSqlIntegrationTests(unittest.TestCase):
    """Run the real queries against the release-path database, then roll back."""

    class Rollback(Exception):
        pass

    def test_close_snapshot_ladder_others_prior_and_evidence_on_postgres(self) -> None:
        import uuid
        from app import main
        from app.instrument_registry import InstrumentRecord, ensure_instruments

        day = date(2097, 6, 17)

        def at(hour: int, minute: int, second: int = 0, on: date = day) -> datetime:
            return datetime(on.year, on.month, on.day, hour, minute, second, tzinfo=CN)

        rows = [
            pool_event("999981.SZ", "收盘仍封", at(15, 0, 5, date(2097, 6, 14))),
            pool_event("999981.SZ", "收盘仍封", at(13, 0, 4)),
            pool_event("999982.SZ", "盘中炸板", at(13, 0, 4), continue_day_text="3连板", continue_day_cnt=3),
            pool_event("999981.SZ", "收盘仍封", at(14, 59, 21), continue_day_text="2连板", continue_day_cnt=2),
            chain_event("999981.SZ", "收盘仍封", 2, at(9, 40, 2)),
            chain_event("999982.SZ", "盘中炸板", 3, at(9, 40, 2)),
            {"symbol": "999983.SZ", "source": "longhuvip_composite_close_limit_derived", "occurred_at": at(16, 5),
             "available_at": at(16, 5), "body": json.dumps({"ts_code": "999983.SZ", "status": "收盘封板"})},
        ]
        outcome: dict = {}
        try:
            with main.db.transaction() as connection:
                ensure_instruments(connection, [InstrumentRecord(symbol=symbol, exchange="SZ", name="测", source="test")
                                                for symbol in ("999981.SZ", "999982.SZ", "999983.SZ")], source="test")
                for row in rows:
                    event_type = "limit_chain" if "a_share_limit_up_ladder" in row["body"] else "limit_up_pool"
                    connection.execute(
                        """INSERT INTO quant.market_events(event_id,symbol,event_type,occurred_at,available_at,source,
                               title,body,url,content_sha256,event_identity_key)
                           VALUES(%s,%s,%s,%s,%s,%s,%s,%s,NULL,%s,NULL)""",
                        (uuid.uuid4(), row["symbol"], event_type, row["occurred_at"], row["available_at"],
                         row["source"], event_type, row["body"], uuid.uuid4().hex),
                    )

                class SameTransaction:
                    @contextmanager
                    def transaction(self):
                        yield connection

                outcome["events"] = load_close_limit_events(connection, day)
                outcome["prior"] = load_prior_close_pool(connection, day)
                outcome["evidence"] = limit_event_evidence(SameTransaction(), day)
                raise self.Rollback
        except self.Rollback:
            pass

        events = outcome["events"]
        self.assertEqual([(item["row_data"]["ts_code"], item["row_data"]["tag"]) for item in events.pool],
                         [("999981.SZ", "2连板")])
        self.assertEqual([(item["row_data"]["ts_code"], item["row_data"]["nums"]) for item in events.ladder],
                         [("999981.SZ", 2)])
        self.assertEqual([row["symbol"] for row in events.others], ["999983.SZ"])
        self.assertEqual(datetime.fromisoformat(events.snapshot["observed_at"]), at(14, 59, 21))
        self.assertEqual([(row["ts_code"], row["trade_date"]) for row in outcome["prior"]], [("999981.SZ", "20970614")])
        evidence = outcome["evidence"]
        self.assertEqual(evidence["status"], "completed")
        self.assertEqual((evidence["limit_up_pool"]["rows"], evidence["limit_up_pool"]["symbols"],
                          evidence["limit_up_pool"]["snapshots"]), (3, 2, 2))
        self.assertEqual((evidence["limit_chain"]["rows"], evidence["limit_chain"]["symbols"]), (2, 2))

if __name__ == "__main__":
    unittest.main()
