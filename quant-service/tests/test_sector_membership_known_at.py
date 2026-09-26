import unittest
from datetime import date, datetime, timezone
from unittest.mock import MagicMock

from app import sector_membership_repository as repository


class KnownAtIsNeverMovedLaterTests(unittest.TestCase):
    """known_at is the replay boundary; a refresh must not push it later."""

    def _statements(self):
        connection = MagicMock()
        observed_at = datetime(2026, 9, 25, 9, 0, tzinfo=timezone.utc)
        repository.persist_ths_snapshot(
            connection, "ths_concept", "885001.TI",
            [{"con_code": "600000.SH", "in_date": "20200101", "out_date": None}],
            "tushare", observed_at,
            ensure_instrument=lambda *_args: None,
            parse_date=lambda value: date(2020, 1, 1) if value else None,
        )
        repository.persist_observed_snapshot(
            connection, "longhu_ths_industry", "881101", [{"code": "600000.SH"}], "longhu", observed_at,
            member_symbol=lambda row: row["code"], ensure_instrument=lambda *_args: None,
        )
        return [call.args[0] for call in connection.execute.call_args_list]

    def test_upserts_keep_the_earliest_known_at(self):
        upserts = [sql for sql in self._statements()
                   if "ON CONFLICT" in sql and "INSERT INTO quant.sector_membership_history" in sql]
        self.assertEqual(len(upserts), 2)
        for sql in upserts:
            self.assertIn("known_at=LEAST(sector_membership_history.known_at,EXCLUDED.known_at)", sql)
            self.assertNotIn("known_at=EXCLUDED.known_at", sql)

    def test_interval_closes_leave_known_at_untouched(self):
        closes = [sql for sql in self._statements() if sql.lstrip().startswith("UPDATE quant.sector_membership_history")]
        self.assertEqual(len(closes), 2)
        for sql in closes:
            self.assertNotIn("known_at", sql)

    def test_the_batched_writer_follows_the_same_rule(self):
        from pathlib import Path
        source = Path(repository.__file__).read_text(encoding="utf-8")
        self.assertNotIn("known_at=EXCLUDED.known_at", source)
        self.assertNotIn("known_at=%s", source)


if __name__ == "__main__":
    unittest.main()
