from __future__ import annotations

from datetime import date
from unittest import TestCase

from app.intraday_sector_report_service import build_intraday_sector_report_from_membership


class _Transaction:
    def __init__(self, connection):
        self.connection = connection

    def __enter__(self):
        return self.connection

    def __exit__(self, *_):
        return False


class _Connection:
    def __init__(self, responses):
        self.responses = iter(responses)

    def execute(self, *_args):
        return _Result(next(self.responses))


class _Result:
    def __init__(self, rows):
        self.rows = rows

    def fetchall(self):
        return self.rows


class _Db:
    def __init__(self, responses):
        self.connection = _Connection(responses)

    def transaction(self):
        return _Transaction(self.connection)


class IntradaySectorReportServiceTests(TestCase):
    def test_a_ths_named_board_takes_the_fuyao_members_and_an_unmatched_one_the_eastmoney_label(self):
        db = _Db([
            [{"taxonomy_key": "fuyao_ths_concept", "sector_key": "885959.TI", "label": "PCB"}],
            [{"taxonomy_key": "fuyao_ths_concept", "sector_key": "885959.TI", "symbol": "000001.SZ"}],
            [{"sector_key": "BK9999", "symbol": "000002.SZ", "label": "东财独有"}],
            [{"taxonomy_key": "eastmoney_concept", "latest_trade_date": date(2026, 10, 9), "rows": 380}],
            [{"api_name": "moneyflow", "latest_trade_date": "20261008", "symbols": 1, "rows": 1}],
            [{"api_name": "rt_k", "latest_available_at": "2026-10-08T01:00:00Z", "rows": 1}],
        ])
        quotes = {"000001.SZ": {"symbol": "000001.SZ", "main_net_inflow": 12, "turnover": 100},
                  "000002.SZ": {"symbol": "000002.SZ", "main_net_inflow": 5, "turnover": 10}}
        report, coverage, sector_context, stock_context, realtime_context = build_intraday_sector_report_from_membership(
            db, ("concept",), [[{"行业": "ＰＣＢ", "流入资金": 10, "流出资金": 3},
                                {"行业": "东财独有", "流入资金": 1, "流出资金": 2},
                                {"行业": "没有成分", "流入资金": 1, "流出资金": 1}]],
            quotes, 10, date(2026, 10, 9), number=lambda value: float(value) if value is not None else None,
        )
        pcb, eastmoney_only, unmapped = report
        self.assertEqual((pcb["sector_key"], pcb["membership_taxonomy_key"], pcb["membership_join"]),
                         ("885959.TI", "fuyao_ths_concept", "ths_board_name"))
        self.assertEqual(pcb["net_inflow"], 7.0)
        self.assertEqual([stock["symbol"] for stock in pcb["top_stocks"]], ["000001.SZ"])
        self.assertEqual(pcb["flow_source"], "ths_10jqka_via_akshare")
        self.assertEqual((eastmoney_only["sector_key"], eastmoney_only["membership_join"]), ("BK9999", "eastmoney_board_label"))
        self.assertEqual((unmapped["membership_join"], unmapped["mapped_members"]), ("unmapped", 0))
        self.assertEqual(coverage["concept"]["boards_with_members"], 2)
        self.assertEqual(coverage["concept"]["ths_name_join"]["unmatched"], ["东财独有", "没有成分"])
        self.assertNotIn("ths_concept", coverage, "the frozen close section is gone")
        self.assertEqual(sector_context[0]["taxonomy_key"], "eastmoney_concept")
        self.assertEqual(stock_context[0]["api_name"], "moneyflow")
        self.assertEqual(realtime_context[0]["api_name"], "rt_k")


if __name__ == "__main__":
    import unittest
    unittest.main()
