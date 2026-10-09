from __future__ import annotations

import unittest
from datetime import date, datetime, timezone

from app.request_models import StrategyReviewRequest
from app.strategy_review_service import build, completed_for_checkpoint


class _Result:
    def __init__(self, row=None, rows=None):
        self._row = row
        self._rows = rows or []

    def fetchone(self):
        return self._row

    def fetchall(self):
        return self._rows


class _Connection:
    def __init__(self):
        self.calls = []

    def execute(self, statement, params=()):
        self.calls.append((statement, params))
        if "FROM quant.market_events" in statement or "FROM quant.canonical_bars_daily" in statement:
            return _Result(rows=[])
        return _Result({
            "observed_at": datetime(2026, 8, 21, 7, 30, tzinfo=timezone.utc),
            "summary": {"priced_symbols": 2},
            "payload": {"items": [{"sector_key": "demo", "net_inflow": 1}]},
            "source_status": {},
        })


class StrategyReviewServiceTests(unittest.TestCase):
    def test_projection_is_read_only_when_persistence_is_disabled(self):
        connection = _Connection()
        review = build(
            connection,
            StrategyReviewRequest(session="close", as_of_date=date(2026, 8, 21), persist=False),
            market_state=lambda items: ("mixed", {"items": len(items)}),
            index_breadth_context=lambda *args: {"quality_flags": []},
            analyst_context=lambda *args: {"execution_eligible": False},
            json_safe=lambda value: value,
        )
        self.assertEqual(review["status"], "completed")
        self.assertEqual(review["longhu_next_session_context"]["status"], "empty")
        self.assertEqual(review["market_state"], "mixed")
        self.assertNotIn("review_key", review)
        self.assertEqual(review["data_boundary"]["automation"], "no broker order submission")
        daily_query = next(sql for sql, _params in connection.calls if "FROM quant.canonical_bars_daily" in sql)
        daily_params = next(params for sql, params in connection.calls if "FROM quant.canonical_bars_daily" in sql)
        self.assertIn("b.available_at<=%s", daily_query)
        self.assertIn("b.quality_status='fresh'", daily_query)
        self.assertEqual(daily_params[1], datetime(2026, 8, 21, 7, 30, tzinfo=timezone.utc))
        longhu_query = next(sql for sql, _params in connection.calls if "raw_market_observations" in sql)
        self.assertIn("::date<%s", longhu_query)

    def test_lhb_comes_from_the_dragon_tiger_rows_captured_by_the_checkpoint(self):
        class Connection(_Connection):
            def execute(self, statement, params=()):
                if "lhb_ths" in statement:
                    self.calls.append((statement, params))
                    return _Result(rows=[{
                        "symbol": "601086.SH", "source": "fuyao_ths",
                        "available_at": datetime(2026, 8, 21, 7, 0, tzinfo=timezone.utc),
                        "payload": {"capability": "a_share_dragon_tiger_list", "trade_date": "2026-08-21",
                                    "name": "国芳集团", "net_value": 12599059.02, "buy_value": 112179750.0,
                                    "sell_value": 99580690.98, "range_days": 1, "hot_money_net_value": -2185780.98},
                    }])
                return super().execute(statement, params)

        connection = Connection()
        review = build(
            connection,
            StrategyReviewRequest(session="close", as_of_date=date(2026, 8, 21), persist=False),
            market_state=lambda items: ("mixed", {}),
            index_breadth_context=lambda *args: {"quality_flags": []},
            analyst_context=lambda *args: {"execution_eligible": False},
            json_safe=lambda value: value,
        )
        capital = review["short_term_review"]["capital_and_lhb"]
        self.assertEqual((capital["lhb_symbol_count"], capital["lhb_evidence_status"]), (1, "completed"))
        self.assertIsNone(capital["institution_records"])
        self.assertEqual(capital["lhb_seat_evidence_status"], "unavailable")
        self.assertIn("Fuyao", review["data_boundary"]["lhb"])
        self.assertNotIn("Tushare", review["data_boundary"]["lhb"])
        lhb_params = next(params for sql, params in connection.calls if "lhb_ths" in sql)
        # Only rows captured by the review's own snapshot, for its trade date.
        snapshot = datetime(2026, 8, 21, 7, 30, tzinfo=timezone.utc)
        self.assertEqual(lhb_params, (snapshot, snapshot, "2026-08-21"))
        self.assertFalse(any("tushare_raw_records" in sql for sql, _params in connection.calls))

    def test_completed_checkpoint_requires_completed_persisted_report(self):
        class Connection:
            def __init__(self, row): self.row = row
            def execute(self, statement, params=()):
                self.statement, self.params = statement, params
                return _Result(self.row)

        completed = Connection({"?column?": 1})
        self.assertTrue(completed_for_checkpoint(completed, date(2026, 8, 21), "close"))
        self.assertIn("report->>'status'='completed'", completed.statement)
        self.assertEqual(completed.params, (date(2026, 8, 21), "close"))
        self.assertFalse(completed_for_checkpoint(Connection(None), date(2026, 8, 21), "close"))


if __name__ == "__main__":
    unittest.main()
