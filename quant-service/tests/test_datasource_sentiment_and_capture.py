"""Self-computed sentiment, the Fuyao capture fixes, batched persistence and the review fix."""

import json
import unittest
from contextlib import contextmanager
from datetime import datetime, timezone

from app.datasources.derived.market_sentiment import (
    concept_strength, distribution, ladder, limit_ratio_pct, market_sentiment_snapshot, promotion_by_layer,
)
from app.market_event_capture import capture, normalize_fuyao_events
from app.public_market_repository import persist_market_events, persist_timed_observations
from app.short_term_review import build_short_term_review


class SentimentTests(unittest.TestCase):
    def test_limit_ratio_by_board(self):
        self.assertEqual(limit_ratio_pct("600000.SH"), 10.0)
        self.assertEqual(limit_ratio_pct("300750.SZ"), 20.0)
        self.assertEqual(limit_ratio_pct("688981.SH"), 20.0)
        self.assertEqual(limit_ratio_pct("920819.BJ"), 30.0)
        self.assertEqual(limit_ratio_pct("302132.SZ"), 20.0)
        # Main-board ST moved from 5% to 10% on 2026-07-06.
        self.assertEqual(limit_ratio_pct("600000.SH", "*ST 某某", "20260703"), 5.0)
        self.assertEqual(limit_ratio_pct("600000.SH", "*ST 某某", "20260706"), 10.0)

    def test_ladder_and_layered_promotion(self):
        today = ladder([{"thscode": "A", "continue_day_cnt": 3}, {"thscode": "B", "continue_day_cnt": 1},
                        {"symbol": "C", "board_count": 2}])
        self.assertEqual(today["max_board_height"], 3)
        self.assertEqual(today["distribution"], {"1": 1, "2": 1, "3": 1})
        self.assertEqual(today["gaps_below_top"], [])
        promotion = promotion_by_layer({"X": 1, "Y": 1, "C": 1, "A": 2, "Z": 4}, {"A", "C", "B"})
        self.assertEqual(promotion["layers"]["1to2"], {"base": 3, "promoted": 1, "rate": 0.3333})
        self.assertEqual(promotion["layers"]["2to3"], {"base": 1, "promoted": 1, "rate": 1.0})
        self.assertEqual(promotion["layers"]["3to4"]["rate"], None)
        self.assertEqual(promotion["overall"]["promoted"], 2)

    def test_distribution_buckets_and_limits(self):
        snapshot = {
            "600000.SH": {"pct_change": 9.95}, "300750.SZ": {"pct_change": 9.95}, "600001.SH": {"pct_change": 0.0},
            "600002.SH": {"pct_change": -9.9}, "600003.SH": {"pct_change": 2.0}, "600004.SH": {"pct_change": None},
        }
        result = distribution(snapshot)
        self.assertEqual(result["observed"], 5)
        self.assertEqual(result["at_limit_up_by_price"], 1)       # 300750 is a 20% board
        self.assertEqual(result["at_limit_down_by_price"], 1)
        self.assertEqual(result["flat"], 1)
        self.assertEqual(result["buckets"]["gt_7"], 2)
        self.assertEqual(result["buckets"]["0_to_3"], 1)
        self.assertEqual(sum(result["buckets"].values()), 4)      # flat is not in a bucket

    def test_concept_strength_with_and_without_membership(self):
        quotes = {"885001.TI": {"price_change_ratio_pct": 3.0, "index_name": "A"},
                  "885002.TI": {"price_change_ratio_pct": 1.0, "index_name": "B"}}
        plain = concept_strength(quotes, None, set(), {})
        self.assertEqual(plain[0]["index_code"], "885001.TI")
        self.assertIsNone(plain[0]["limit_up_members"])
        members = {"885001.TI": {f"60000{i}.SH" for i in range(5)}, "885002.TI": {f"30000{i}.SZ" for i in range(5)}}
        snapshot = {symbol: {"pct_change": 10.0} for symbol in members["885002.TI"]}
        ranked = concept_strength(quotes, members, set(members["885002.TI"]), snapshot)
        self.assertEqual(ranked[0]["index_code"], "885002.TI")   # sealed members outweigh the index move

    def test_snapshot_record(self):
        record = market_sentiment_snapshot(
            limit_up_rows=[{"thscode": "600000.SH", "continue_day_cnt": 2}, {"thscode": "600001.SH"}],
            broken_rows=[{"thscode": "600002.SH"}, {"thscode": "600000.SH"}],
            limit_down_rows=[{"thscode": "600009.SH"}],
            previous_limit_up_rows=[{"symbol": "600000.SH", "board_count": 1}, {"symbol": "600005.SH", "board_count": 1}],
            snapshot_rows=[{"symbol": "600000.SH", "pct_change": 10.0, "turnover": 1e8},
                           {"symbol": "600005.SH", "pct_change": -2.0, "turnover": 5e7}],
            previous_turnover_total=1e8,
        )
        self.assertEqual(record["limit_up_count"], 2)
        self.assertEqual(record["broken_count"], 1)          # a sealed name is not also broken
        self.assertEqual(record["seal_rate"], 0.6667)
        self.assertEqual(record["prior_limit_up_today"]["all"]["premium_pct"], 4.0)
        self.assertEqual(record["prior_limit_up_today"]["all"]["red_rate"], 0.5)
        self.assertEqual(record["turnover"]["change_pct"], 50.0)
        self.assertEqual(record["promotion"]["layers"]["1to2"]["rate"], 0.5)


class FuyaoCaptureTests(unittest.IsolatedAsyncioTestCase):
    observed = datetime(2026, 9, 18, 1, 27, tzinfo=timezone.utc)   # 09:27 Shanghai

    def test_limit_down_pool_is_normalized(self):
        rows = normalize_fuyao_events("a_share_limit_down_pool", {"item": [{"thscode": "002528.SZ", "name": "X"}]}, self.observed)
        self.assertEqual(rows[0]["event_type"], "limit_down_pool")
        self.assertIn("202609180127", rows[0]["event_identity_key"])

    async def test_pools_are_paged_and_attention_waits_for_its_cadence(self):
        calls = []
        stored_events, stored_observations = [], []

        async def fetch(capability, params):
            calls.append((capability, dict(params)))
            if capability in {"a_share_limit_up_pool", "a_share_limit_break_pool", "a_share_limit_down_pool"}:
                page = params["page"]
                return {"item": [{"thscode": f"60000{page}.SH"}], "pagination": {"pages": 2}}
            if capability == "a_share_auction_short_term_benchmark":
                return {"date": "2026-09-18", "item": [{"thscode": "600127.SH", "auction_pct": -0.92}]}
            if capability in {"a_share_hot_stock_list", "a_share_skyrocket_list"}:
                return {"timestamp": 1789738612008, "item": [{"thscode": "601091.SH", "rank": 1}]}
            return {"item": []}

        async def persist(_provider, rows):
            stored_events.extend(rows)
            return len(rows)

        async def persist_observations(_provider, capability, rows):
            stored_observations.extend((capability, row) for row in rows)
            return len(rows)

        state = {}
        result = await capture(self.observed, fetch=fetch, persist=persist,
                               persist_observations=persist_observations, state=state)
        self.assertEqual(result["capabilities"]["a_share_limit_up_pool"]["received"], 2)
        self.assertIn("a_share_auction_short_term_benchmark", result["capabilities"])
        self.assertEqual(state["auction_benchmark_date"], "2026-09-18")
        self.assertEqual({capability for capability, _row in stored_observations},
                         {"a_share_hot_stock_list", "a_share_skyrocket_list"})
        calls.clear()
        await capture(self.observed, fetch=fetch, persist=persist, persist_observations=persist_observations, state=state)
        self.assertNotIn("a_share_hot_stock_list", {capability for capability, _params in calls})
        self.assertNotIn("a_share_auction_short_term_benchmark", {capability for capability, _params in calls})

    async def test_auction_uses_100_code_batches_of_equities(self):
        sizes = []

        async def fetch(capability, params):
            if capability == "a_share_auction_snapshot":
                codes = params["thscodes"].split(",")
                sizes.append(len(codes))
                return {"item": [{"thscode": code} for code in codes], "auction_phase": "closed"}
            return {"item": []}

        async def persist(_provider, rows):
            return len(rows)

        symbols = [f"{600000 + index}.SH" for index in range(250)] + ["000001.SH", "399001.SZ"]
        result = await capture(self.observed, fetch=fetch, persist=persist, include_auction=True, auction_symbols=symbols)
        self.assertEqual(sizes, [100, 100, 50])
        self.assertEqual(result["auction"]["received"], 250)
        self.assertEqual(result["auction"]["requested"], 250)


class RecordingCursor:
    def __init__(self, calls):
        self.calls = calls

    def executemany(self, sql, rows):
        self.calls.append((" ".join(sql.split())[:60], list(rows)))

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


class RecordingDatabase:
    def __init__(self):
        self.calls = []

    @contextmanager
    def transaction(self):
        calls = self.calls

        class Connection:
            def cursor(self):
                return RecordingCursor(calls)

        yield Connection()


class BatchedPersistenceTests(unittest.TestCase):
    def test_events_are_written_in_three_batched_statements(self):
        database = RecordingDatabase()
        rows = [
            {"ts_code": "600000.SH", "event_type": "news_flash", "title": "a", "event_identity_key": "k1",
             "published_at": "2026-09-18T10:00:00+08:00"},
            {"ts_code": "600000.SH", "event_type": "announcement", "title": "b", "published_at": "2026-09-18T10:01:00+08:00"},
            {"ts_code": "600002", "event_type": "news_flash", "title": "malformed symbol is rejected"},
            {"ts_code": "600001.SH", "event_type": "news_flash", "title": ""},
        ]
        self.assertEqual(persist_market_events(database, "cls_telegraph", rows), 2)
        self.assertEqual(len(database.calls), 3)
        instruments, keyed, content_keyed = database.calls
        self.assertEqual(len(instruments[1]), 1)          # one instrument, deduplicated
        self.assertEqual(keyed[1][0][-1], "k1")
        self.assertIsNone(content_keyed[1][0][-1])        # announcement has no pool identity

    def test_timed_observations_keep_their_clocks_and_ignore_repeats(self):
        database = RecordingDatabase()
        stored = persist_timed_observations(database, "cls_telegraph", "news_flash", [
            {"ts_code": None, "flash_id": "1", "effective_at": "2026-09-18T10:00:00+08:00",
             "available_at": "2026-09-18T10:01:00+08:00"},
        ])
        self.assertEqual(stored, 1)
        sql, rows = database.calls[0]
        self.assertIn("raw_market_observations", sql)
        provider, capability, symbol, effective, available, basis, _sha, payload, _normalized = rows[0]
        self.assertEqual((provider, capability, symbol), ("cls_telegraph", "news_flash", None))
        self.assertIsNone(basis)
        self.assertEqual(effective.isoformat(), "2026-09-18T02:00:00+00:00")
        self.assertEqual(available.isoformat(), "2026-09-18T02:01:00+00:00")
        self.assertNotIn("available_at", payload.obj)     # identical facts hash identically
        self.assertEqual(persist_timed_observations(database, "x", "y", []), 0)


class ShortTermReviewDedupTests(unittest.TestCase):
    def test_minute_rows_count_once_per_symbol(self):
        minute_rows = [
            {"event_type": "limit_up_pool", "symbol": "600000.SH",
             "body": json.dumps({"raw": {"continue_day_cnt": 3}}), "occurred_at": minute}
            for minute in range(240)
        ] + [{"event_type": "limit_down_pool", "symbol": "600009.SH", "body": "{}"} for _ in range(120)]
        review = build_short_term_review(event_rows=minute_rows, daily_rows=[], board_summary={})
        self.assertEqual(review["market_emotion"]["limit_up_count"], 1)
        self.assertEqual(review["market_emotion"]["limit_down_count"], 1)
        self.assertEqual(review["ladder"]["highest_board_count"], 3)   # reads Fuyao's continue_day_cnt


if __name__ == "__main__":
    unittest.main()
