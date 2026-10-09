"""The decoded 开盘啦 limit review and 选股宝 pool, checked against each other on real data."""

from __future__ import annotations

import json
import unittest
from datetime import date, datetime
from pathlib import Path

from app.datasources.sources.longhu_limit_review import UNDECODED, decode_review, market_counts
from app.datasources.sources.xuangubao_pool import decode_pool

FIXTURES = Path(__file__).parent / "fixtures"
REVIEW = json.loads((FIXTURES / "longhu_limit_review_20261008.json").read_text(encoding="utf-8"))["payload"]
POOL = json.loads((FIXTURES / "xuangubao_limit_up_20261008.json").read_text(encoding="utf-8"))["data"]
SESSION = date(2026, 10, 8)


class LimitReviewDecodeTests(unittest.TestCase):
    def test_a_stock_row_decodes_to_named_fields(self):
        rows = {row["symbol"]: row for row in decode_review(REVIEW)}
        stock = rows["002058.SZ"]
        self.assertEqual(stock["name"], "紫竹高科")
        self.assertEqual(stock["trade_date"], "2026-10-08")
        self.assertEqual(stock["first_limit_up_at"], "2026-10-08T09:25:00+08:00")
        self.assertEqual((stock["board_count"], stock["board_label"]), (3, "3连板"))
        self.assertEqual(stock["seal_amount"], 142263552.0)
        self.assertEqual(stock["theme"], "固态电池")
        self.assertIn("固态电池", stock["concepts"])
        self.assertEqual(stock["plates"], [{"code": "801004", "name": "锂电池"}])
        self.assertEqual(sorted(stock["undecoded"]), sorted(str(position) for position in UNDECODED))

    def test_a_stock_filed_under_two_plates_is_one_row_with_both(self):
        first = REVIEW["list"][0]
        doubled = {**REVIEW, "list": [first, {**first, "ZSCode": "801999", "ZSName": "另一题材"}]}
        rows = [row for row in decode_review(doubled) if row["symbol"] == "002058.SZ"]
        self.assertEqual(len(rows), 1)
        self.assertEqual([plate["code"] for plate in rows[0]["plates"]], ["801004", "801999"])

    def test_a_first_seal_outside_the_review_session_is_dropped(self):
        plate = REVIEW["list"][0]
        stale = [list(plate["StockList"][0])]
        stale[0][6] = stale[0][6] - 86400
        rows = decode_review({**REVIEW, "list": [{**plate, "StockList": stale}]})
        self.assertIsNone(rows[0]["first_limit_up_at"])

    def test_market_counts_keep_the_unproven_field_under_its_vendor_name(self):
        counts = market_counts(REVIEW)
        self.assertEqual((counts["limit_up"], counts["limit_down"]), (43, 13))
        self.assertIn("yestRase", counts)


class XuangubaoPoolTests(unittest.TestCase):
    def test_the_pool_adds_last_seal_breaks_and_the_board_count(self):
        rows = {row["symbol"]: row for row in decode_pool(POOL, "limit_up", SESSION)}
        stock = rows["002058.SZ"]
        self.assertEqual(stock["first_limit_up_at"], "2026-10-08T09:25:00+08:00")
        self.assertEqual(stock["last_limit_up_at"], "2026-10-08T09:30:42+08:00")
        self.assertEqual((stock["break_times"], stock["limit_up_days"]), (1, 3))
        self.assertTrue(stock["related_plates"])


class PoolSymbolTests(unittest.TestCase):
    def test_shanghai_written_as_ss_is_kept(self):
        from app.datasources.sources.xuangubao_pool import pool_symbol

        self.assertEqual([pool_symbol(code) for code in ("600825.SS", "002058.SZ", "920438.BJ", "510300.SS")],
                         ["600825.SH", "002058.SZ", "920438.BJ", None])


class CrossSourceTests(unittest.TestCase):
    """The decoding is only as good as its agreement with an independent source."""

    def test_first_seal_time_and_board_count_agree_with_xuangubao(self):
        longhu = {row["symbol"]: row for row in decode_review(REVIEW)}
        xuangubao = {row["symbol"]: row for row in decode_pool(POOL, "limit_up", SESSION)}
        shared = sorted(set(longhu) & set(xuangubao))
        self.assertGreaterEqual(len(shared), 8)
        for symbol in shared:
            lh, xg = longhu[symbol], xuangubao[symbol]
            gap = abs((datetime.fromisoformat(lh["first_limit_up_at"])
                       - datetime.fromisoformat(xg["first_limit_up_at"])).total_seconds())
            self.assertLessEqual(gap, 60, symbol)
            self.assertEqual(lh["board_count"], xg["limit_up_days"], symbol)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
