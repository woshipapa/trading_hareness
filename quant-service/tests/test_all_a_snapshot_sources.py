import json
import unittest
from datetime import datetime, timezone
from pathlib import Path

from app.all_a_snapshot_sources import compare, project_tdx_snapshot_rows


FIXTURE = Path(__file__).parent / "fixtures" / "all_a_snapshot_fuyao_tdx_20261012.json"


class TdxSnapshotProjectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = json.loads(FIXTURE.read_text())
        cls.observed_at = datetime(2026, 10, 12, 7, 31, 46, tzinfo=timezone.utc)
        cls.projected = project_tdx_snapshot_rows(cls.fixture["tdx_rows"], cls.observed_at)
        cls.by_symbol = {row["symbol"]: row for row in cls.projected}
        cls.fuyao_by_symbol = {row["symbol"]: row for row in cls.fixture["fuyao_rows"]}

    def test_projects_common_rows_into_fuyao_shape(self):
        self.assertEqual(len(self.projected), len(self.fixture["tdx_rows"]))
        for symbol, fuyao in self.fuyao_by_symbol.items():
            projected = self.by_symbol[symbol]
            for field in ("symbol", "price", "pct_change", "price_source", "price_observed_at"):
                expected = self.observed_at.isoformat() if field == "price_observed_at" else fuyao[field]
                if field == "price_source":
                    expected = "tdx_public_all_a_snapshot"
                if field == "pct_change":
                    self.assertAlmostEqual(projected[field], expected, places=5, msg=symbol)
                else:
                    self.assertEqual(projected[field], expected, symbol)
            self.assertAlmostEqual(projected["volume"], fuyao["volume"], delta=100, msg=symbol)
            self.assertAlmostEqual(projected["turnover"], fuyao["turnover"], delta=1000, msg=symbol)
            self.assertEqual(projected["raw"]["thscode"], symbol)
            self.assertEqual(projected["raw"]["ticker"], next(row["code"] for row in self.fixture["tdx_rows"] if row["symbol"] == symbol))
            tdx = next(row for row in self.fixture["tdx_rows"] if row["symbol"] == symbol)
            self.assertEqual(projected["raw"]["last_price"], tdx["price"])
            self.assertEqual(projected["raw"]["prev_price"], tdx["pre_close"])
            self.assertEqual(projected["raw"]["open_price"], tdx["open"])
            self.assertEqual(projected["raw"]["high_price"], tdx["high"])
            self.assertEqual(projected["raw"]["low_price"], tdx["low"])
            self.assertEqual(projected["raw"]["price_change"], round(tdx["price"] - tdx["pre_close"], 2))
            self.assertEqual(projected["raw"]["price_change_ratio_pct"], tdx["pct_change"])
            self.assertEqual(projected["raw"]["volume"], projected["volume"])
            self.assertEqual(projected["raw"]["turnover"], projected["turnover"])

    def test_projects_symbol_missing_from_fuyao(self):
        row = self.by_symbol["600293.SH"]
        self.assertEqual(row["symbol"], "600293.SH")
        self.assertEqual(row["volume"], 0)
        self.assertEqual(row["turnover"], 0.0)

    def test_drops_non_positive_prices(self):
        rows = [{"symbol": "000001.SZ", "price": 0, "pre_close": 1, "pct_change": -100,
                 "volume_lots": 1, "amount": 1, "open": 1, "high": 1, "low": 1}]
        self.assertEqual(project_tdx_snapshot_rows(rows, self.observed_at), [])


class TdxSnapshotComparisonTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixture = json.loads(FIXTURE.read_text())
        cls.fuyao_rows = fixture["fuyao_rows"]
        cls.tdx_rows = project_tdx_snapshot_rows(fixture["tdx_rows"], datetime(2026, 10, 12, tzinfo=timezone.utc))

    def test_compares_fixture_without_verdict(self):
        result = compare(self.fuyao_rows, self.tdx_rows, 2.5)

        self.assertEqual(result["symbols_in_both"], 14)
        self.assertEqual(result["only_in_fuyao"], [])
        self.assertEqual(result["only_in_tdx"], ["600293.SH"])
        self.assertEqual(result["price"]["within_one_tick"], 1.0)
        self.assertEqual(result["price"]["within_0_1_pct"], 1.0)
        self.assertEqual(result["price"]["within_0_5_pct"], 1.0)
        self.assertEqual(result["price"]["largest_relative_gap"], 0.0)
        self.assertEqual(result["price"]["largest_relative_gap_symbol"], "000001.SZ")
        self.assertLess(result["pct_change"]["largest_absolute_gap"], 0.000001)
        self.assertAlmostEqual(result["volume"]["median_ratio"], 0.9999990450538223)
        self.assertEqual(result["volume"]["within_1_pct"], 1.0)
        self.assertAlmostEqual(result["turnover"]["median_ratio"], 0.9999999892985673)
        self.assertEqual(result["turnover"]["within_1_pct"], 1.0)
        self.assertEqual(result["seconds_apart"], 2.5)

    def test_moved_price_changes_only_price_measurements(self):
        moved = [dict(row) for row in self.tdx_rows]
        moved[0]["price"] = 11.80

        result = compare(self.fuyao_rows, moved, 1.0)

        self.assertEqual(result["price"]["within_one_tick"], 13 / 14)
        self.assertEqual(result["price"]["within_0_1_pct"], 13 / 14)
        self.assertEqual(result["price"]["within_0_5_pct"], 13 / 14)
        self.assertAlmostEqual(result["price"]["largest_relative_gap"], 0.07 / 11.73)
        self.assertEqual(result["price"]["largest_relative_gap_symbol"], "000001.SZ")
        self.assertLess(result["pct_change"]["largest_absolute_gap"], 0.000001)
        self.assertEqual(result["seconds_apart"], 1.0)
