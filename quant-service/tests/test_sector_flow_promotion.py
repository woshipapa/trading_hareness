"""Promotion reads whichever provider answered, and one slow write never
silences another taxonomy."""

from __future__ import annotations

import asyncio
import contextlib
import unittest
from datetime import date, datetime, timezone
from types import SimpleNamespace

from app.annual_daily_backfill import AnnualDailyBackfill
from app.ths_sector_flows import CONCEPT_PERSIST_TIMEOUT_SECONDS, sync_concept_signals

AT = datetime(2026, 9, 22, 17, 0, tzinfo=timezone.utc)


class RecordingConnection:
    def __init__(self, rows):
        self._rows = rows
        self.persisted: list[tuple[str, str]] = []
        self.queries: list[str] = []

    def execute(self, sql, params=None):
        self.queries.append(sql)
        rows = self._rows if "tushare_raw_records" in sql else []
        return SimpleNamespace(fetchall=lambda: rows, fetchone=lambda: None)

    def cursor(self):
        connection = self

        class Cursor:
            @contextlib.contextmanager
            def copy(self, _statement):
                yield SimpleNamespace(write_row=lambda row: connection.persisted.append(("staged", str(row[0]))))

        return Cursor()


class Database:
    def __init__(self, rows):
        self.connection = RecordingConnection(rows)

    @contextlib.contextmanager
    def transaction(self):
        yield self.connection


class PromotionTests(unittest.TestCase):
    def rows(self, provider):
        return [{"provider_key": provider, "available_at": AT,
                 "row_data": {"ts_code": "881101.TI", "trade_date": "20260922", "name": "概念"}}]

    def promote(self, provider, monkey):
        db = Database(self.rows(provider))
        job = AnnualDailyBackfill(db, date(2026, 9, 22), date(2026, 9, 22))
        monkey.append(db)
        return job.promote_stored_sector_flows(), db

    def test_rows_from_the_fallback_provider_are_promoted(self):
        import app.annual_daily_backfill as module

        seen: list[str] = []
        original = module._persist_sector_flow
        module._persist_sector_flow = lambda connection, provider_key, available_at, **kw: seen.append(provider_key)
        try:
            counts, _db = self.promote("tushare_super_get", [])
        finally:
            module._persist_sector_flow = original
        # Three mappings, each with the one stored row: the provider is carried
        # through instead of being pinned to the SDK that never answered here.
        self.assertEqual(seen, ["tushare_super_get"] * 3)
        self.assertEqual(counts["moneyflow_cnt_ths"], 1)

    def test_the_query_no_longer_pins_a_provider(self):
        import app.annual_daily_backfill as module

        original = module._persist_sector_flow
        module._persist_sector_flow = lambda *args, **kwargs: None
        try:
            _counts, db = self.promote("tushare_backup", [])
        finally:
            module._persist_sector_flow = original
        select = next(sql for sql in db.connection.queries if "tushare_raw_records" in sql)
        self.assertNotIn("tushare_super_sdk", select)
        self.assertIn("provider_key,row_data,available_at", select)


class ConceptSyncTests(unittest.TestCase):
    def run_sync(self, *, concept_fails):
        persisted: list[str] = []

        async def run_database_blocking(action, *args, timeout_seconds=10):
            name = getattr(action, "__name__", "?")
            if name == "persist_concept_flow" and concept_fails:
                raise TimeoutError("database executor timed out")
            persisted.append(f"{name}:{timeout_seconds}")
            return None

        async def fetch_catalog(request):
            return {"status": "completed", "provider": "tushare_super_get", "request_key": f"k:{request.api_name}"}

        async def load_rows(key):
            return [{"ts_code": "881101.TI", "name": "概念", "pct_chg": 3.2, "cons_nums": 5}]

        result = asyncio.run(sync_concept_signals(
            SimpleNamespace(trade_date=date(2026, 9, 22), provider="auto"),
            trade_date=lambda: date(2026, 9, 22), fetch_catalog=fetch_catalog,
            fetch_request=lambda **kwargs: SimpleNamespace(**kwargs), load_rows=load_rows,
            run_database_blocking=run_database_blocking, db=None,
            upsert_taxonomy=lambda *a, **k: None, upsert_sector=lambda *a, **k: None,
            decimal_or_none=lambda value: value, json_value=lambda value: value,
            observed_at=lambda: AT, http_exception=RuntimeError,
        ))
        return result, persisted

    def test_a_failed_concept_write_still_leaves_limit_strength_written(self):
        result, persisted = self.run_sync(concept_fails=True)
        self.assertEqual(result["sources"]["concept_flow"]["status"], "failed")
        self.assertEqual(result["sources"]["limit_strength"]["status"], "completed")
        self.assertEqual(persisted, [f"persist_limit_strength:{CONCEPT_PERSIST_TIMEOUT_SECONDS}"])
        self.assertEqual(result["status"], "partial")

    def test_both_writes_get_more_than_the_ten_second_default(self):
        result, persisted = self.run_sync(concept_fails=False)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(persisted, [f"persist_concept_flow:{CONCEPT_PERSIST_TIMEOUT_SECONDS}",
                                     f"persist_limit_strength:{CONCEPT_PERSIST_TIMEOUT_SECONDS}"])
        self.assertGreater(CONCEPT_PERSIST_TIMEOUT_SECONDS, 10)


if __name__ == "__main__":
    unittest.main()
