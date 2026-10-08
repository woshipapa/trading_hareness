"""A board directory is written in one statement, in key order."""

from __future__ import annotations

import json
import os
import unittest

from app.sector_catalog_repository import upsert_sectors


class _Connection:
    def __init__(self):
        self.calls = []

    def execute(self, statement, params):
        self.calls.append((statement, params))


class UpsertSectorsTests(unittest.TestCase):
    def test_one_statement_sorted_and_deduplicated(self):
        connection = _Connection()
        stored = upsert_sectors(connection, "eastmoney_concept", [
            ("BK0002", "二", {"n": 2}), ("BK0001", "一", {"n": 1}), ("BK0002", "二改", {"n": 3}),
        ])
        self.assertEqual(stored, 2)
        self.assertEqual(len(connection.calls), 1)
        taxonomy, keys, labels, metadata = connection.calls[0][1]
        self.assertEqual(taxonomy, "eastmoney_concept")
        self.assertEqual(keys, ["BK0001", "BK0002"])
        self.assertEqual(labels, ["一", "二改"], "the last row for a key wins")
        self.assertEqual(json.loads(metadata[1]), {"n": 3})

    def test_an_empty_directory_writes_nothing(self):
        connection = _Connection()
        self.assertEqual(upsert_sectors(connection, "eastmoney_concept", []), 0)
        self.assertEqual(connection.calls, [])

    def test_values_json_cannot_encode_are_stringified_not_fatal(self):
        from datetime import date
        connection = _Connection()
        upsert_sectors(connection, "t", [("S1", "x", {"listed": date(2026, 1, 2)})])
        self.assertEqual(json.loads(connection.calls[0][1][3][0]), {"listed": "2026-01-02"})


@unittest.skipUnless(os.getenv("PGHOST"), "requires the compose PostgreSQL service")
class UpsertSectorsSqlTests(unittest.TestCase):
    def setUp(self) -> None:
        import psycopg
        from psycopg.rows import dict_row
        self.connection = psycopg.connect(
            host=os.getenv("PGHOST"), port=os.getenv("PGPORT", "5432"), dbname=os.getenv("PGDATABASE", "n8n"),
            user=os.getenv("PGUSER", "n8n"), password=os.getenv("PGPASSWORD", ""), row_factory=dict_row,
        )
        self.connection.execute("INSERT INTO quant.providers(provider_key,label) VALUES('zz_test','t') ON CONFLICT DO NOTHING")
        self.connection.execute(
            "INSERT INTO quant.sector_taxonomies(taxonomy_key,label,provider_key) VALUES('zz_test_tax','t','zz_test') "
            "ON CONFLICT DO NOTHING")

    def tearDown(self) -> None:
        self.connection.rollback()
        self.connection.close()

    def test_insert_then_update_in_place(self):
        upsert_sectors(self.connection, "zz_test_tax", [("S1", "one", {"v": 1}), ("S2", "two", {"v": 2})])
        upsert_sectors(self.connection, "zz_test_tax", [("S1", "one renamed", {"v": 9})])
        rows = self.connection.execute(
            "SELECT sector_key,label,metadata FROM quant.sectors WHERE taxonomy_key='zz_test_tax' ORDER BY sector_key"
        ).fetchall()
        self.assertEqual([(row["sector_key"], row["label"]) for row in rows], [("S1", "one renamed"), ("S2", "two")])
        self.assertEqual(rows[0]["metadata"], {"v": 9})


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
