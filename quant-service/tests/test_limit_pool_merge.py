from __future__ import annotations

import json
import unittest
from datetime import date, datetime, timezone

from app.limit_event_fallback import event_limit_record
from app.limit_pool_merge import merge_limit_pool_sources

CLOSE = datetime(2026, 9, 18, 6, 59, 30, tzinfo=timezone.utc)   # 14:59:30 Shanghai


def fuyao_pool_row(symbol: str, name: str, **fields) -> dict:
    """A Fuyao close-snapshot row as ``limit_event_repository`` projects it."""
    body = {"capability": "a_share_limit_up_pool", "thscode": symbol, "ticker": symbol[:6], "name": name,
            "is_st": False, "is_new": False, "last_price": 24.85, "price_change_ratio_pct": 10.0044,
            "limit_up_time": "09:30", "limit_up_reason": "AI短剧+短剧出海+数字阅读",
            "continue_day_text": "首板", "continue_day_cnt": 1, "seal_money": 94507035, "max_seal_money": 216515565.0,
            **fields}
    return event_limit_record({"symbol": symbol, "source": "fuyao_ths", "occurred_at": CLOSE, "available_at": CLOSE,
                               "body": json.dumps(body, ensure_ascii=False)}, trade_date=date(2026, 9, 18))


def merge(primary, secondary):
    return merge_limit_pool_sources(
        primary, secondary,
        json_safe=lambda value: json.loads(json.dumps(value, ensure_ascii=False, default=str)),
        number=lambda value: float(value) if value not in (None, "") else None,
    )


class LimitPoolMergeTests(unittest.TestCase):
    def test_merge_uses_symbol_identity_and_only_enriches_missing_primary_fields(self) -> None:
        result = merge(
            [fuyao_pool_row("603533.SH", "掌阅科技", continue_day_text="2连板", continue_day_cnt=2),
             {"row_data": {"ts_code": "not-a-symbol"}}],
            [{"symbol": "603533.SH", "source": "akshare", "body": {
                "名称": "不应覆盖", "最新价": 11.0, "成交额": 123.0, "换手率": 7.5, "炸板次数": 0, "连板数": 2,
            }},
             {"symbol": "600000.SH", "source": "akshare", "body": {"名称": "独立标的", "连板数": 1}}],
        )
        by_symbol = {item["ts_code"]: item for item in result["items"]}
        self.assertEqual(set(by_symbol), {"603533.SH", "600000.SH"})
        fuyao = by_symbol["603533.SH"]
        self.assertEqual(fuyao["name"], "掌阅科技")
        self.assertEqual(fuyao["price"], 24.85)
        self.assertEqual(fuyao["limit_amount"], 94507035.0)
        self.assertEqual(fuyao["tag"], "2连板")
        self.assertEqual(fuyao["lu_desc"], "AI短剧+短剧出海+数字阅读")
        # Fields the Fuyao pool lacks are filled from the other source, never invented.
        self.assertEqual((fuyao["amount"], fuyao["turnover_rate"], fuyao["open_num"]), (123.0, 7.5, 0.0))
        self.assertEqual(fuyao["sources"], ["market_events:fuyao_ths", "eastmoney_stock_zt_pool_em"])
        self.assertNotIn("tushare_limit_list_ths", fuyao["sources"])
        self.assertEqual(by_symbol["600000.SH"]["tag"], "首板")
        coverage = result["coverage"]
        self.assertEqual(coverage["status"], "two_source_union")
        self.assertEqual(coverage["union_count"], 2)
        self.assertEqual((coverage["tushare_count"], coverage["eastmoney_count"]), (1, 2))
        self.assertEqual(coverage["primary_sources"], ["market_events:fuyao_ths"])
        self.assertEqual(coverage["secondary_sources"], ["eastmoney_stock_zt_pool_em"])

    def test_invalid_json_body_is_local_empty_evidence_not_a_failure(self) -> None:
        result = merge([], [{"symbol": "000001.SZ", "body": "not-json"}])
        self.assertEqual(result["coverage"]["status"], "single_source_only")
        self.assertEqual(result["items"][0]["ts_code"], "000001.SZ")
        self.assertEqual(result["items"][0]["tag"], "首板")

    def test_other_event_sources_without_a_close_pool_keep_provider_provenance(self) -> None:
        settled = {"ts_code": "000001.SZ", "trade_date": "20260918", "name": "平安银行", "limit_type": "涨停池",
                   "status": "收盘封板", "close": 12.1, "limit_price": 12.1, "turnover_rate": 3.2, "limit_amount": None}
        result = merge([], [{"symbol": "000001.SZ", "event_type": "limit_up_pool",
                             "source": "longhuvip_composite_close_limit_derived", "body": json.dumps(settled)}])
        self.assertEqual(result["coverage"]["status"], "market_event_fallback")
        item = result["items"][0]
        self.assertEqual(item["sources"], ["market_events:longhuvip_composite_close_limit_derived"])
        self.assertTrue(item["source_fallback"])
        self.assertEqual((item["name"], item["turnover_rate"]), ("平安银行", 3.2))

    def test_no_pool_at_all_is_reported_unavailable(self) -> None:
        coverage = merge([], [])["coverage"]
        self.assertEqual(coverage["status"], "unavailable")
        self.assertEqual(coverage["union_count"], 0)


if __name__ == "__main__":
    unittest.main()
