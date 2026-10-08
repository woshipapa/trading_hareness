"""Promotion reads whichever provider answered, and one slow write never
silences another taxonomy."""

from __future__ import annotations

import asyncio
import contextlib
import unittest
from datetime import date, datetime, timezone
from types import SimpleNamespace

from app.ths_sector_flows import CONCEPT_PERSIST_TIMEOUT_SECONDS, sync_concept_signals

AT = datetime(2026, 9, 22, 17, 0, tzinfo=timezone.utc)


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
