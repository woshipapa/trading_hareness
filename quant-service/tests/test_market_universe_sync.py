"""The authoritative all-A listing, from Fuyao's A-share ticker list (decision 0005)."""

import asyncio
import unittest
from datetime import date
from types import SimpleNamespace

from app import market_universe_sync
from app.market_universe_sync import PAGE_SIZE, listed_rows


class _Connection:
    def __init__(self):
        self.statements = []

    def execute(self, statement, values=None):
        self.statements.append((" ".join(statement.split()), values))
        return SimpleNamespace(fetchone=lambda: None, fetchall=lambda: [], rowcount=0)


class _Database:
    def __init__(self, connection):
        self.connection = connection

    def transaction(self):
        connection = self.connection

        class _Ctx:
            def __enter__(self_inner):
                return connection

            def __exit__(self_inner, *_args):
                return False

        return _Ctx()


class ExecutorSaturated(RuntimeError):
    pass


def _ticker(code, *, asset_type="a-share", list_date="1991-04-03", end_date=None):
    return {"thscode": code, "ticker": code[:6], "name": f"n{code[:6]}", "exchange": code[-2:],
            "asset_type": asset_type, "list_date": list_date, "end_date": end_date}


def _market(sh=2400, sz=2900, bj=280):
    # Shanghai equity codes start 600/601/603 (602 is not one), so spread across them.
    shanghai = [f"{prefix}{index:03d}.SH" for prefix in ("600", "601", "603") for index in range(1000)]
    return ([_ticker(code) for code in shanghai[:sh]]
            + [_ticker(f"{index + 1:06d}.SZ") for index in range(sz)]
            + [_ticker(f"{920000 + index}.BJ") for index in range(bj)])


class ListedRowsTests(unittest.TestCase):
    def test_only_listed_a_share_equities_become_stock_basic_rows(self):
        rows = listed_rows([
            _ticker("600000.SH", list_date="1999-11-10"),
            _ticker("883970.TI", asset_type="a-share-index"),
            _ticker("200028.SZ"),
            _ticker("600001.SH", end_date="2026-09-30"),
            _ticker("600002.SH", end_date="2026-12-31"),
        ], date(2026, 10, 9))
        self.assertEqual(sorted(rows), ["600000.SH", "600002.SH"])
        self.assertEqual(rows["600000.SH"]["list_date"], "19991110", "the YYYYMMDD form the normalizer reads")
        self.assertEqual(rows["600002.SH"]["delist_date"], "20261231")


class MarketUniverseSyncTests(unittest.TestCase):
    """instruments.list_date comes from here, and the volume baseline needs it."""

    def _run(self, *, items, minimum_rows=5000, error=None):
        requested = []

        async def fetch(capability, params):
            requested.append((capability, params))
            if error is not None:
                raise error
            offset = params["offset"]
            return {"item": items[offset:offset + params["limit"]]}

        async def run_database_blocking(action, *args, **kwargs):
            return action(*args) if args else action()

        connection = _Connection()
        result = asyncio.run(market_universe_sync.sync(
            SimpleNamespace(universe_key="all_a", provider="auto", minimum_rows=minimum_rows),
            fetch=fetch, cn_date=lambda: date(2026, 10, 9),
            persist_rows=lambda _connection, api_name, _key, rows, provider, _at: len(rows),
            run_database_blocking=run_database_blocking, db=_Database(connection),
            safe_error_detail=lambda text, _limit: text, executor_saturated_error=ExecutorSaturated,
            record_provider_success=lambda *_args, **_kwargs: None,
            record_provider_failure=lambda *_args, **_kwargs: None,
        ))
        return result, requested, connection

    def test_the_whole_list_is_paged_to_its_end(self):
        result, requested, connection = self._run(items=_market())
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["imported"], 5580)
        self.assertEqual([params["offset"] for _capability, params in requested], [0, 1000, 2000, 3000, 4000, 5000])
        self.assertTrue(all(params["asset_type"] == "a-share" and params["limit"] == PAGE_SIZE
                            for _capability, params in requested))
        retired = [values for sql, values in connection.statements if sql.startswith("UPDATE quant.universe_members SET enabled=false")]
        self.assertEqual(len(retired[0][1]), 5580, "members absent from a complete listing are retired")

    def test_a_short_listing_changes_nothing(self):
        result, _requested, connection = self._run(items=_market(sh=1000, sz=1000, bj=100))
        self.assertEqual(result["status"], "blocked")
        self.assertIn("expected at least 5000", result["reason"])
        self.assertFalse([sql for sql, _ in connection.statements if "universe_members" in sql])

    def test_a_listing_missing_an_exchange_changes_nothing(self):
        result, _requested, connection = self._run(items=_market(sh=2600, sz=3000, bj=0))
        self.assertEqual(result["status"], "blocked")
        self.assertIn("['BJ']", result["reason"])
        self.assertFalse([sql for sql, _ in connection.statements if "universe_members" in sql])

    def test_a_saturated_executor_is_blocked_without_a_provider_failure(self):
        result, _requested, connection = self._run(items=[], error=ExecutorSaturated("local capacity"))
        self.assertEqual(result["status"], "blocked")
        self.assertFalse([sql for sql, _ in connection.statements if "status='failed'" in sql])


if __name__ == "__main__":
    unittest.main()
