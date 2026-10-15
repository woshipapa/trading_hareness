from pathlib import Path
import unittest


class ScanSignalReadIndexMigrationTests(unittest.TestCase):
    def test_migration_is_forward_from_quote_freshness_head_and_reversible(self):
        source = (Path(__file__).parents[1] / "migrations/versions/20261016_ow0127_scan_signal_read_index.py").read_text()
        self.assertIn('revision = "20261016_ow0127"', source)
        self.assertIn('down_revision = "20261016_ow0126"', source)
        self.assertIn("intraday_signal_events_scan_read_idx", source)
        self.assertIn("DROP INDEX IF EXISTS quant.intraday_signal_events_scan_read_idx", source)


if __name__ == "__main__":
    unittest.main()
