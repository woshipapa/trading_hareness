"""One session's limit-up detail: the Longhu review and 选股宝's pool, merged and compared."""

from __future__ import annotations

import json
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace

from app.limit_detail_read_model import POOL_CAPABILITY_PREFIX, REVIEW_CAPABILITY, limit_detail_day

FIXTURES = Path(__file__).parent / "fixtures"
REVIEW = json.loads((FIXTURES / "longhu_limit_review_20261008.json").read_text(encoding="utf-8"))["payload"]
POOL = json.loads((FIXTURES / "xuangubao_limit_up_20261008.json").read_text(encoding="utf-8"))["data"]
SESSION = date(2026, 10, 8)


class _Connection:
    def __init__(self, review_rows, pool_rows):
        self.review_rows, self.pool_rows = review_rows, pool_rows

    def execute(self, _sql, params):
        assert "LIKE" not in _sql, "a LIKE prefix cannot use the capability index"
        rows = self.review_rows if params[0] == [REVIEW_CAPABILITY] else self.pool_rows
        return SimpleNamespace(fetchall=lambda: rows)


def review_rows(source_date="2026-10-08"):
    return [{"capability": REVIEW_CAPABILITY,
             "normalized": {"exchange_date": "2026-10-08", "source_date": source_date, "payload": plate}}
            for plate in REVIEW["list"]]


def pool_rows():
    return [{"capability": POOL_CAPABILITY_PREFIX + "limit_up",
             "normalized": {"exchange_date": "2026-10-08", "payload": row}} for row in POOL]


class LimitDetailTests(unittest.TestCase):
    def test_both_sources_merge_per_stock_with_their_agreement(self):
        day = limit_detail_day(_Connection(review_rows(), pool_rows()), SESSION)
        stocks = {stock["symbol"]: stock for stock in day["stocks"]}
        stock = stocks["600825.SH"]
        self.assertEqual(stock["sources"], ["longhuvip", "xuangubao"])
        self.assertEqual(stock["board_count"], 8)
        self.assertEqual(stock["break_times"], 0)
        self.assertTrue(stock["one_word"], "sealed in the auction and never broke")
        self.assertLessEqual(stock["agreement"]["first_seal_gap_seconds"], 60)
        self.assertTrue(stock["agreement"]["board_count_equal"])
        self.assertEqual(stocks["002058.SZ"]["one_word"], False, "opened at the limit but broke once")
        self.assertEqual(day["coverage"]["both"], 9)

    def test_a_review_dated_another_session_is_not_used(self):
        day = limit_detail_day(_Connection(review_rows(source_date="2026-10-07"), []), SESSION)
        self.assertEqual(day["stocks"], [])
        self.assertEqual(day["status"], "missing")

    def test_an_undated_review_is_used_only_when_its_seals_fall_on_the_session(self):
        undated = review_rows(source_date=None)
        self.assertTrue(limit_detail_day(_Connection(undated, []), SESSION)["stocks"])
        self.assertEqual(limit_detail_day(_Connection(undated, []), date(2026, 10, 9))["stocks"], [])
        self.assertEqual(limit_detail_day(_Connection(undated, []), SESSION)["coverage"]["review_pages_without_own_date"], 2)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
