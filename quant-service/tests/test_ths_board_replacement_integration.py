"""Real-PostgreSQL coverage for the THS board replacements (Tushare retired, decision 0005).

The Fuyao membership refresh is new SQL against the existing schema, so it
runs here against the migrated test database inside one transaction that is
rolled back.  Dates are in 2099 and symbols 9999xx so nothing collides with
other suites.
"""

from __future__ import annotations

import contextlib
import os
import unittest
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

CN = ZoneInfo("Asia/Shanghai")
DAY_ONE, DAY_TWO = date(2099, 3, 2), date(2099, 3, 3)
CONCEPT = "fuyao_ths_concept"


class _Rollback(Exception):
    pass


class _Within:
    """Hands every repository call the test's connection, so all of it rolls back."""

    def __init__(self, connection):
        self._connection = connection

    @contextlib.contextmanager
    def transaction(self):
        yield self._connection


def _at(day: date, hour: int, minute: int, second: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, second, tzinfo=CN).astimezone(timezone.utc)


@unittest.skipUnless(os.getenv("PGHOST"), "requires the compose PostgreSQL service")
class ThsBoardReplacementIntegrationTests(unittest.TestCase):
    def run_rolled_back(self, body):
        from app.main import db

        try:
            with db.transaction() as connection:
                body(_Within(connection), connection)
                raise _Rollback
        except _Rollback:
            pass

    def test_a_daily_refresh_records_only_membership_changes(self):
        from app.fuyao_ths_membership_repository import (
            boards_due, listed_counts, persist_catalog, persist_member_snapshot, record_member_failure, refresh_progress,
        )
        from app.sector_membership_repository import point_in_time_membership_predicate

        def members(*symbols):
            return {symbol: {"name": f"测{symbol[:6]}", "thscode": symbol, "index_code": "885431.TI"} for symbol in symbols}

        def body(database, connection):
            persist_catalog(database, CONCEPT, "同花顺概念（Fuyao）", "cn_concept",
                            [("885431.TI", "新能源汽车"), ("885566.TI", "大飞机")], DAY_ONE)
            self.assertEqual(listed_counts(database, [CONCEPT, "fuyao_ths_industry"], DAY_ONE), {CONCEPT: 2})
            due, listed = boards_due(database, CONCEPT, DAY_ONE, 10)
            self.assertEqual(([row["sector_key"] for row in due], listed), (["885431.TI", "885566.TI"], 2))

            first = persist_member_snapshot(database, CONCEPT, "885431.TI", members("999901.SZ", "999902.SZ"),
                                            _at(DAY_ONE, 15, 40))
            self.assertEqual((first["opened"], first["closed"], first["state"]), (2, 0, "completed"))
            record_member_failure(database, CONCEPT, "885566.TI", DAY_ONE, "Fuyao business error")
            due, _listed = boards_due(database, CONCEPT, DAY_ONE, 10)
            self.assertEqual([row["sector_key"] for row in due], ["885566.TI"])     # failed, still under the cap
            progress = refresh_progress(database, [CONCEPT], DAY_ONE)[CONCEPT]
            self.assertEqual((progress["listed"], progress["completed_or_empty"], progress["failed"]), (2, 1, 1))

            persist_catalog(database, CONCEPT, "同花顺概念（Fuyao）", "cn_concept", [("885431.TI", "新能源汽车")], DAY_TWO)
            second = persist_member_snapshot(database, CONCEPT, "885431.TI", members("999902.SZ", "999903.SZ"),
                                             _at(DAY_TWO, 15, 40))
            self.assertEqual((second["opened"], second["closed"]), (1, 1))
            again = persist_member_snapshot(database, CONCEPT, "885431.TI", members("999902.SZ", "999903.SZ"),
                                            _at(DAY_TWO, 16, 10))
            self.assertEqual((again["opened"], again["closed"]), (0, 0))

            rows = connection.execute(
                """SELECT symbol,effective_from,effective_to,known_at FROM quant.sector_membership_history
                    WHERE taxonomy_key=%s AND sector_key='885431.TI' ORDER BY symbol,effective_from""", (CONCEPT,),
            ).fetchall()
            self.assertEqual([(row["symbol"], row["effective_from"], row["effective_to"]) for row in rows], [
                ("999901.SZ", DAY_ONE, DAY_ONE), ("999902.SZ", DAY_ONE, None), ("999903.SZ", DAY_TWO, None),
            ])
            # The unchanged member keeps the moment it was first known.
            self.assertEqual(rows[1]["known_at"], _at(DAY_ONE, 15, 40))
            predicate = point_in_time_membership_predicate("member")
            for day, expected in ((DAY_ONE, ["999901.SZ", "999902.SZ"]), (DAY_TWO, ["999902.SZ", "999903.SZ"])):
                visible = connection.execute(
                    f"""SELECT symbol FROM quant.sector_membership_history member
                         WHERE taxonomy_key=%s AND sector_key='885431.TI' AND {predicate} ORDER BY symbol""",
                    (CONCEPT, day, day, day),
                ).fetchall()
                self.assertEqual([row["symbol"] for row in visible], expected, day)
            self.assertEqual(boards_due(database, CONCEPT, DAY_TWO, 10)[0], [])

        self.run_rolled_back(body)

    def test_membership_refresh_status_reports_the_fuyao_taxonomies(self):
        from app.fuyao_ths_membership_repository import persist_catalog, persist_member_snapshot
        from app.sector_read_model import concept_member_backfill_status

        def body(database, _connection):
            persist_catalog(database, CONCEPT, "同花顺概念（Fuyao）", "cn_concept",
                            [("885431.TI", "新能源汽车"), ("885566.TI", "大飞机")], DAY_ONE)
            persist_member_snapshot(database, CONCEPT, "885431.TI", {"999921.SZ": {"name": "测"}}, _at(DAY_ONE, 15, 40))
            status = concept_member_backfill_status(database, DAY_ONE, automatic_enabled=True, batch_size=25)
            self.assertEqual((status["taxonomy_key"], status["source"]), (CONCEPT, "fuyao_ths"))
            self.assertEqual((status["total_concepts"], status["mapped_concepts"], status["receipt_mapped_concepts"]), (2, 1, 1))
            self.assertFalse(status["complete"])
            self.assertEqual(status["taxonomies"][CONCEPT]["listed"], 2)
            self.assertEqual(status["automatic"], {"enabled": True, "batch_size": 25})

        self.run_rolled_back(body)


if __name__ == "__main__":
    unittest.main()
