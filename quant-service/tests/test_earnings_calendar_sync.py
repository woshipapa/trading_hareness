"""Reporting-calendar period selection and the Eastmoney datacenter normalization."""

from __future__ import annotations

import asyncio
import unittest
from datetime import date

from app.earnings_calendar_sync import (
    PROVIDER, REPORT_KEYS, normalize_disclosure_rows, normalize_express_rows, normalize_forecast_rows,
    reporting_period, sync,
)

PERIOD = date(2026, 6, 30)
# Shapes as RPT_PUBLIC_OP_NEWPREDICT returned them on 2026-10-09: one announcement, one row per indicator.
FORECAST = [
    {"SECUCODE": "600187.SH", "NOTICE_DATE": "2026-08-19 00:00:00", "REPORT_DATE": "2026-06-30 00:00:00",
     "PREDICT_FINANCE_CODE": "005", "PREDICT_AMT_LOWER": -18000000, "PREDICT_AMT_UPPER": -15000000,
     "ADD_AMP_LOWER": -18.58, "ADD_AMP_UPPER": 1.19, "PREDICT_TYPE": "续亏", "PREYEAR_SAME_PERIOD": -15180000},
    {"SECUCODE": "600187.SH", "NOTICE_DATE": "2026-08-19 00:00:00", "REPORT_DATE": "2026-06-30 00:00:00",
     "PREDICT_FINANCE_CODE": "004", "PREDICT_AMT_LOWER": 2650000, "PREDICT_AMT_UPPER": 3150000,
     "ADD_AMP_LOWER": 114.47, "ADD_AMP_UPPER": 117.19, "PREDICT_TYPE": "扭亏", "PREYEAR_SAME_PERIOD": -18320000,
     "PREDICT_CONTENT": "预计盈利265万元至315万元。", "CHANGE_REASON_EXPLAIN": "投资收益"},
    {"SECUCODE": "600187.SH", "NOTICE_DATE": "2026-07-08 00:00:00", "REPORT_DATE": "2026-06-30 00:00:00",
     "PREDICT_FINANCE_CODE": "004", "PREDICT_AMT_LOWER": 2000000, "PREDICT_TYPE": "扭亏"},
    {"SECUCODE": "688208.SH", "NOTICE_DATE": "2026-08-18 00:00:00", "REPORT_DATE": "2026-06-30 00:00:00",
     "PREDICT_FINANCE_CODE": "006", "PREDICT_AMT_LOWER": 2640000000, "PREDICT_TYPE": "略增"},
]


class ReportingPeriodTests(unittest.TestCase):
    def test_returns_the_most_recent_settled_quarter_end(self):
        self.assertEqual(reporting_period(date(2026, 8, 26)), date(2026, 6, 30))
        self.assertEqual(reporting_period(date(2026, 11, 5)), date(2026, 9, 30))

    def test_a_just_ended_quarter_is_skipped_until_its_calendar_exists(self):
        self.assertEqual(reporting_period(date(2026, 7, 2)), date(2026, 3, 31),
                         "a two-day-old quarter has no registered disclosure calendar yet")
        self.assertEqual(reporting_period(date(2026, 7, 10)), date(2026, 6, 30))

    def test_january_falls_back_to_the_previous_year_end(self):
        self.assertEqual(reporting_period(date(2026, 1, 20)), date(2025, 12, 31))


class DisclosureTests(unittest.TestCase):
    def test_first_appointment_latest_reschedule_and_actual_date(self):
        rows = normalize_disclosure_rows([
            {"SECUCODE": "920438.BJ", "REPORT_DATE": "2026-06-30 00:00:00", "FIRST_APPOINT_DATE": "2026-08-20 00:00:00",
             "FIRST_CHANGE_DATE": "2026-08-25 00:00:00", "SECOND_CHANGE_DATE": "2026-08-28 00:00:00",
             "ACTUAL_PUBLISH_DATE": "2026-08-28 00:00:00"},
            {"SECUCODE": "000533.SZ", "REPORT_DATE": "2026-06-30 00:00:00", "FIRST_APPOINT_DATE": "2026-08-20 00:00:00"},
        ], PERIOD)
        by_symbol = {row["symbol"]: row for row in rows}
        self.assertEqual(by_symbol["920438.BJ"]["pre_date"], date(2026, 8, 20))
        self.assertEqual(by_symbol["920438.BJ"]["modify_date"], date(2026, 8, 28))
        self.assertEqual(by_symbol["920438.BJ"]["actual_date"], date(2026, 8, 28))
        self.assertIsNone(by_symbol["000533.SZ"]["modify_date"])
        self.assertIsNone(by_symbol["000533.SZ"]["actual_date"])

    def test_other_periods_and_b_shares_are_dropped(self):
        rows = normalize_disclosure_rows([
            {"SECUCODE": "600362.SH", "REPORT_DATE": "2026-03-31 00:00:00", "FIRST_APPOINT_DATE": "2026-04-26 00:00:00"},
            {"SECUCODE": "200028.SZ", "REPORT_DATE": "2026-06-30 00:00:00", "FIRST_APPOINT_DATE": "2026-08-18 00:00:00"},
        ], PERIOD)
        self.assertEqual(rows, [])


class ForecastTests(unittest.TestCase):
    def test_one_row_per_announcement_from_the_parent_net_profit_indicator(self):
        rows = {(row["symbol"], row["ann_date"]): row for row in normalize_forecast_rows(FORECAST, PERIOD)}
        latest = rows[("600187.SH", date(2026, 8, 19))]
        self.assertEqual(latest["forecast_type"], "扭亏")
        self.assertEqual((latest["net_profit_min"], latest["net_profit_max"]), (265.0, 315.0), "10k CNY, as Tushare held it")
        self.assertEqual(latest["last_parent_net"], -1832.0)
        self.assertAlmostEqual(latest["p_change_min"], 114.47)
        self.assertEqual(latest["first_ann_date"], date(2026, 7, 8))
        self.assertEqual(len(rows), 3)

    def test_an_announcement_without_a_profit_row_keeps_its_type_but_no_profit_range(self):
        row = next(row for row in normalize_forecast_rows(FORECAST, PERIOD) if row["symbol"] == "688208.SH")
        self.assertEqual(row["forecast_type"], "略增")
        self.assertIsNone(row["net_profit_min"])

    def test_a_row_without_a_notice_date_is_dropped(self):
        self.assertEqual(normalize_forecast_rows(
            [{"SECUCODE": "600187.SH", "REPORT_DATE": "2026-06-30 00:00:00", "PREDICT_TYPE": "预增"}], PERIOD), [])


class ExpressTests(unittest.TestCase):
    def test_express_rows_carry_reported_actuals_in_cny(self):
        rows = normalize_express_rows([{
            "SECUCODE": "300124.SZ", "REPORT_DATE": "2026-06-30 00:00:00", "NOTICE_DATE": "2026-08-24 00:00:00",
            "TOTAL_OPERATE_INCOME": 24675251000, "PARENT_NETPROFIT": 2809924100, "BASIC_EPS": 1.04,
            "WEIGHTAVG_ROE": 7.69, "JLRTBZCL": -5.35, "DATATYPE": "2026年 半年报",
        }, {"SECUCODE": "300124.SZ", "REPORT_DATE": "2026-06-30 00:00:00", "NOTICE_DATE": "2026-08-24 00:00:00",
            "TOTAL_OPERATE_INCOME": "--"}], PERIOD)
        self.assertEqual(len(rows), 1, "a repeated announcement collapses to one row")
        self.assertIsNone(rows[0]["revenue"], "an unparseable number becomes null rather than raising")

    def test_parsed_figures(self):
        row = normalize_express_rows([{
            "SECUCODE": "300124.SZ", "REPORT_DATE": "2026-06-30 00:00:00", "UPDATE_DATE": "2026-08-24 00:00:00",
            "PARENT_NETPROFIT": 2809924100, "WEIGHTAVG_ROE": 7.69,
        }], PERIOD)[0]
        self.assertEqual(row["ann_date"], date(2026, 8, 24))
        self.assertEqual((row["n_income"], row["diluted_roe"]), (2809924100.0, 7.69))


class _Connection:
    def __init__(self):
        self.statements = []

    def execute(self, sql, params=None):
        self.statements.append((sql, params))


class _Database:
    def __init__(self):
        self.connection = _Connection()

    def transaction(self):
        database = self

        class _Context:
            def __enter__(self):
                return database.connection

            def __exit__(self, *_exc):
                return False

        return _Context()


class SyncTests(unittest.TestCase):
    def run_sync(self, failing=()):
        fetched = []

        async def fetch(report_key, period):
            fetched.append((report_key, period))
            if report_key in failing:
                raise ValueError("Eastmoney RPT_X returned 10 of 20 rows")
            return {"disclosure_schedule": [{"SECUCODE": "600187.SH", "REPORT_DATE": "2026-06-30 00:00:00",
                                             "FIRST_APPOINT_DATE": "2026-08-19 00:00:00"}],
                    "earnings_forecast": FORECAST, "earnings_express": []}[report_key]

        async def run(action, *args, **_kwargs):
            return action(*args)

        database = _Database()
        result = asyncio.run(sync(date(2026, 10, 9), fetch_period_report=fetch, run_database_blocking=run,
                                  db=database, safe_error_detail=lambda text, _n: text))
        return result, fetched, database

    def test_every_report_is_read_for_the_settled_period_and_stored_under_eastmoney(self):
        result, fetched, database = self.run_sync()
        self.assertEqual(fetched, [(REPORT_KEYS[api], PERIOD) for api in ("disclosure_date", "forecast", "express")])
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["rows"], {"disclosure_date": 1, "forecast": 3, "express": 0})
        self.assertEqual(set(result["providers"].values()), {PROVIDER})
        self.assertTrue(database.connection.statements)
        self.assertTrue(all(PROVIDER in params for _sql, params in database.connection.statements))

    def test_one_failed_report_is_reported_and_the_others_still_land(self):
        result, _fetched, _database = self.run_sync(failing={"earnings_forecast"})
        self.assertEqual(result["status"], "partial")
        self.assertIn("returned 10 of 20 rows", result["errors"]["forecast"])
        self.assertEqual(set(result["rows"]), {"disclosure_date", "express"})


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
