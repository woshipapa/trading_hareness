import unittest
from datetime import date
from unittest.mock import MagicMock

from app.point_in_time_status import pit_st_sql, st_flags_as_of


class PointInTimeStStatusTests(unittest.TestCase):
    def test_a_covered_session_answers_from_dated_evidence(self):
        sql = pit_st_sql("b.symbol", "b.trading_date", "coalesce(i.is_st,false)")
        # Covered session: listed means ST, absent means not ST.
        self.assertIn("st_cov.status_date=b.trading_date AND st_cov.list_status='UNKNOWN'", sql)
        self.assertIn("st_row.symbol=b.symbol AND st_row.status_date=b.trading_date", sql)
        # Uncovered session: the current flag, the only value available.
        self.assertTrue(sql.rstrip().endswith("ELSE coalesce(i.is_st,false) END"))

    def test_st_flags_as_of_reports_its_basis(self):
        connection = MagicMock()
        connection.execute.return_value.fetchall.return_value = [
            {"symbol": "600001.SH", "is_st": True, "st_basis": "dated_stock_st"},
            {"symbol": "600002.SH", "is_st": False, "st_basis": "current_instrument_flag"},
        ]
        result = st_flags_as_of(connection, ["600002.SH", "600001.SH"], date(2025, 3, 3))
        self.assertEqual(result["600001.SH"], {"is_st": True, "st_basis": "dated_stock_st"})
        self.assertEqual(result["600002.SH"]["st_basis"], "current_instrument_flag")
        sql, params = connection.execute.call_args.args
        self.assertEqual(sql.count("%s"), len(params))
        self.assertEqual(params[-1], ["600001.SH", "600002.SH"])

    def test_historical_readers_no_longer_read_the_current_flag_alone(self):
        from pathlib import Path
        app = Path(__file__).resolve().parents[1] / "app"
        for name in ("factor_lab.py", "feature_snapshot_repository.py", "strategy_daily_candidate_ledger.py"):
            source = (app / name).read_text(encoding="utf-8")
            with self.subTest(module=name):
                self.assertTrue("pit_st_sql" in source or "st_flags_as_of" in source)
