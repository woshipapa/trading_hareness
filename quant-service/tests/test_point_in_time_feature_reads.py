"""Regression coverage for availability-aware feature reads.

The feature layer is used by both research and recommendation materialization.
These tests keep the database boundary explicit: a row whose market date is
eligible must still be excluded when the provider made it available after the
decision cutoff.
"""

from datetime import date, datetime
from unittest import mock
from zoneinfo import ZoneInfo
import unittest

from app.feature_read_repository import analyst_feature, latest_tushare_row
from app.feature_snapshot_repository import materialize_feature_snapshot
from app.feature_snapshot_runtime import FeatureSnapshotRuntime, FeatureSnapshotRuntimeDependencies
from app.point_in_time import availability_cutoff, exchange_day_end


CN_TZ = ZoneInfo("Asia/Shanghai")


class AvailabilityAwareFeatureReadTests(unittest.TestCase):
    def test_feature_snapshot_market_and_fundamental_reads_use_the_cutoff(self):
        class Result:
            def fetchall(self):
                return []

            def fetchone(self):
                return None

        class Connection:
            def __init__(self):
                self.calls = []

            def execute(self, sql, params=()):
                self.calls.append((str(sql), params))
                if "FROM quant.universe_membership_history" in str(sql):
                    return type("Members", (), {"fetchall": lambda _self: [{"symbol": "000001.SZ", "name": "Test", "industry": "Tech", "is_st": False}]})()
                if "FROM quant.canonical_bars_daily" in str(sql):
                    return type("Bars", (), {"fetchall": lambda _self: []})()
                return Result()

        connection = Connection()
        cutoff = datetime(2026, 8, 27, 10, 15, tzinfo=CN_TZ)
        materialize_feature_snapshot(
            connection, date(2026, 8, 27), "core", feature_version="pit-test", knowledge_cutoff=cutoff,
            number=float, market_regime=lambda *_: "neutral",
            analyst_text_factor_summary=lambda *_: {"market": {}},
            latest_tushare_row=lambda *_: None, analyst_feature=lambda *_: {},
        )

        bar_sql, bar_params = next(item for item in connection.calls if "FROM quant.canonical_bars_daily" in item[0])
        fundamental_sql, fundamental_params = next(item for item in connection.calls if "FROM quant.daily_fundamentals" in item[0])
        self.assertIn("bar.available_at<=%s", bar_sql)
        self.assertEqual(bar_params[-1], cutoff)
        self.assertIn("available_at<=%s", fundamental_sql)
        self.assertEqual(fundamental_params[-1], cutoff)
    def test_daily_cutoff_is_the_end_of_the_exchange_day_in_shanghai(self):
        cutoff = availability_cutoff(date(2026, 8, 27))

        self.assertEqual(cutoff, exchange_day_end(date(2026, 8, 27)))
        self.assertEqual(cutoff.tzinfo, CN_TZ)

    def test_naive_intraday_cutoff_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "timezone-aware"):
            availability_cutoff(date(2026, 8, 27), datetime(2026, 8, 27, 10, 15))

    def test_latest_tushare_row_passes_an_explicit_availability_cutoff(self):
        connection = mock.MagicMock()
        connection.execute.return_value.fetchall.return_value = [{"row_data": {"ts_code": "000001.SZ"}}]
        cutoff = datetime(2026, 8, 27, 10, 15, tzinfo=CN_TZ)

        result = latest_tushare_row(connection, "moneyflow", "000001.SZ", date(2026, 8, 27), cutoff)

        self.assertEqual(result, {"ts_code": "000001.SZ"})
        sql, params = connection.execute.call_args.args
        self.assertIn("available_at<=%s", sql)
        self.assertEqual(params[-1], cutoff)

    def test_analyst_feature_uses_timestamp_cutoff_instead_of_exchange_date(self):
        connection = mock.MagicMock()
        connection.execute.return_value.fetchall.return_value = []
        cutoff = datetime(2026, 8, 27, 10, 15, tzinfo=CN_TZ)

        result = analyst_feature(connection, "000001.SZ", date(2026, 8, 27), lambda value: float(value or 0), cutoff)

        self.assertEqual(result["claim_count"], 0)
        sql, params = connection.execute.call_args.args
        self.assertIn("o.strategy_available_at<=%s", sql)
        self.assertEqual(params[-1], cutoff)

    def test_feature_snapshot_runtime_carries_the_cutoff_to_materialization(self):
        connection = object()
        # Keep the transaction double deliberately small and explicit.
        class Transaction:
            def __enter__(self):
                return connection

            def __exit__(self, *_args):
                return None

        class Database:
            def transaction(self):
                return Transaction()

        database = Database()
        received = {}

        def materialize(*args, **kwargs):
            received.update(args=args, kwargs=kwargs)
            return {"snapshot_key": "snapshot-1"}

        cutoff = datetime(2026, 8, 27, 10, 15, tzinfo=CN_TZ)
        runtime = FeatureSnapshotRuntime(FeatureSnapshotRuntimeDependencies(
            database=database,
            materialize=materialize,
            feature_version="features-v1",
            number=float,
            market_regime=lambda *_: "neutral",
            analyst_text_factor_summary=lambda *_: {},
            latest_tushare_row=lambda *_: None,
            analyst_feature=lambda *_: {},
        ))

        runtime.build(date(2026, 8, 27), "core", knowledge_cutoff=cutoff)

        self.assertEqual(received["kwargs"]["knowledge_cutoff"], cutoff)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
