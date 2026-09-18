from __future__ import annotations

from datetime import datetime, timezone
import unittest

from app.async_intraday_scan_inputs_repository import enabled_watches, exact_memberships, watchlists
from app.datasources.catalog import NON_SECTOR_GROUPS, NON_SECTOR_LABEL_PATTERN


class _Result:
    def __init__(self, rows):
        self.rows = rows

    async def fetchall(self):
        return self.rows


class _Connection:
    def __init__(self):
        self.calls = []

    async def execute(self, query, params):
        self.calls.append((query, params))
        if "sector_membership_history" in query:
            return _Result([{"taxonomy_key": "ths_concept_flow", "sector_key": "885001.TI", "symbol": "000001.SZ"}])
        return _Result([{"symbol": "000001.SZ", "enabled": True}])


class _Transaction:
    def __init__(self, connection):
        self.connection = connection

    async def __aenter__(self):
        return self.connection

    async def __aexit__(self, *_):
        return False


class _Database:
    def __init__(self):
        self.connection = _Connection()

    def transaction(self):
        return _Transaction(self.connection)


class AsyncIntradayScanInputsRepositoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_watchlist_read_detects_overflow_and_memberships_are_exact_and_point_in_time(self) -> None:
        database = _Database()
        all_watches = await watchlists(database, [], max_symbols=40)
        requested_watches = await watchlists(database, ["000001.SZ"], max_symbols=40)
        memberships = await exact_memberships(database, ["000001.SZ"], datetime(2026, 8, 13, 2, tzinfo=timezone.utc))
        empty_memberships = await exact_memberships(database, [], datetime(2026, 8, 13, 2, tzinfo=timezone.utc))

        self.assertEqual(all_watches[0]["symbol"], "000001.SZ")
        self.assertEqual(requested_watches[0]["symbol"], "000001.SZ")
        self.assertEqual(memberships[0]["taxonomy_key"], "ths_concept_flow")
        self.assertEqual(empty_memberships, [])
        all_query, all_params = database.connection.calls[0]
        requested_query, requested_params = database.connection.calls[1]
        member_query, member_params = database.connection.calls[2]
        self.assertIn("LIMIT %s", all_query)
        self.assertEqual(all_params, (41,))
        self.assertIn("symbol=ANY", requested_query)
        self.assertEqual(requested_params, (["000001.SZ"],))
        self.assertIn("taxonomy_key=ANY(%s)", member_query)
        self.assertEqual(member_params[0], ["000001.SZ"])
        # the strategy's declared taxonomies, identical to the literal they replaced
        self.assertEqual(member_params[-3], ["ths_concept_flow", "ths_index_n", "ths_industry"])
        # qualification lists (融资融券, index constituents) never form a peer group
        self.assertIn("NOT (member.sector_key = ANY(%s))", member_query)
        self.assertEqual(member_params[-2:], (list(NON_SECTOR_GROUPS), NON_SECTOR_LABEL_PATTERN))
        self.assertEqual(member_query.count("%s"), len(member_params))

    async def test_enabled_watch_read_uses_exact_capture_limit_without_overflow_row(self) -> None:
        database = _Database()

        rows = await enabled_watches(database, max_symbols=40)

        self.assertEqual(rows[0]["symbol"], "000001.SZ")
        query, params = database.connection.calls[0]
        self.assertIn("WHERE enabled", query)
        self.assertIn("LIMIT %s", query)
        self.assertEqual(params, (40,))


if __name__ == "__main__":
    unittest.main()
