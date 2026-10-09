"""One document a minute for the all-A cross-section (decision 0009)."""

from __future__ import annotations

import asyncio
import json
import unittest
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch
from zoneinfo import ZoneInfo

from app import minute_cross_section as mcs

CN = ZoneInfo("Asia/Shanghai")
MINUTE = datetime(2026, 10, 9, 10, 30, tzinfo=CN)
METADATA = {"provider": "fuyao_ths", "pages": 2, "status": "fresh", "upstream_timestamp_ms": 1791520200000,
            "cross_sectional": True, "notes": "x" * 300}


def fuyao_row(index: int) -> dict:
    code = f"{600000 + index:06d}.SH" if index % 2 else f"{index:06d}.SZ"
    price = 10.0 + index / 100
    return {
        "symbol": code, "ts_code": code, "price": price, "pct_change": round(index % 20 - 10 + 0.11, 2),
        "turnover": 1_000_000.0 * (index + 1), "volume": 10_000.0 * (index + 1),
        "price_observed_at": "2026-10-09T10:29:58+08:00" if index % 3 else "2026-10-09T10:29:57+08:00",
        "price_source": "fuyao_ths",
        "raw": {"thscode": code, "ticker": code[:6], "last_price": price, "open_price": price - 0.1,
                "high_price": price + 0.2, "low_price": price - 0.3, "prev_price": price - 0.05,
                "price_change": 0.05, "price_change_ratio_pct": round(index % 20 - 10 + 0.11, 2),
                "turnover": 1_000_000.0 * (index + 1), "volume": 10_000.0 * (index + 1)},
        "snapshot_observed_at": MINUTE.isoformat(), "snapshot_metadata": METADATA, "research_only": True,
    }


ROWS = [fuyao_row(index) for index in range(400)]


class DocumentTests(unittest.TestCase):
    def setUp(self):
        self.document = mcs.build_document(ROWS, MINUTE, METADATA)

    def test_the_rows_come_back_exactly(self):
        self.assertEqual(mcs.rows_of(self.document), ROWS)

    def test_repeated_fields_are_stored_once_and_identical_columns_as_aliases(self):
        self.assertEqual(self.document["constants"]["snapshot_metadata"], METADATA)
        self.assertEqual(self.document["constants"]["price_source"], "fuyao_ths")
        self.assertEqual(self.document["aliases"]["ts_code"], "symbol")
        self.assertEqual(self.document["aliases"]["raw.thscode"], "symbol")
        self.assertEqual(self.document["aliases"]["raw.turnover"], "turnover")
        self.assertIn("dict", self.document["columns"]["price_observed_at"], "few distinct timestamps are dictionary-encoded")

    def test_the_document_is_a_fraction_of_the_rows_it_replaces(self):
        rows_bytes = 2 * sum(len(json.dumps(row, ensure_ascii=False)) for row in ROWS)   # normalized + payload
        document_bytes = len(json.dumps(self.document, ensure_ascii=False))
        self.assertLess(document_bytes, rows_bytes / 4, (document_bytes, rows_bytes))

    def test_rows_are_found_by_symbol_and_a_missing_field_stays_missing(self):
        picked = mcs.rows_for(self.document, ["600001.SH", "000002.SZ", "999999.SH"])
        self.assertEqual(sorted(picked), ["000002.SZ", "600001.SH"])
        self.assertEqual(picked["600001.SH"], ROWS[1])
        sparse = [dict(ROWS[0]), {k: v for k, v in ROWS[1].items() if k != "price_source"}]
        self.assertEqual(mcs.rows_of(mcs.build_document(sparse, MINUTE)), sparse)

    def test_a_one_row_minute_is_still_found_by_symbol(self):
        single = mcs.build_document([ROWS[1]], MINUTE)
        self.assertEqual(mcs.rows_for(single, ["600001.SH"])["600001.SH"], ROWS[1])


class _Database:
    def __init__(self):
        self.statements = []

    def transaction(self):
        database = self

        class Context:
            def __enter__(self):
                def execute(sql, params=()):
                    database.statements.append((sql, params))
                    return SimpleNamespace(fetchone=lambda: None)
                return SimpleNamespace(execute=execute)

            def __exit__(self, *exc):
                return False
        return Context()


class StorageTests(unittest.TestCase):
    def test_a_minute_is_one_insert_under_its_own_capability(self):
        database = _Database()
        result = mcs.persist_document(database, MINUTE, ROWS, METADATA)
        lock, check, (sql, params) = database.statements
        self.assertIn("pg_advisory_xact_lock", lock[0])
        self.assertIn("symbol IS NULL", check[0])
        self.assertTrue(result["written"])
        self.assertIn("raw_market_observations", sql)
        self.assertEqual(params[:2], ("fuyao_ths", "a_share_minute_cross_section"))
        self.assertEqual(result["rows"], 400)
        stored = json.loads(params[5])
        self.assertEqual(mcs.rows_of(stored), ROWS)

    def test_reads_take_the_newest_or_the_first_document_in_a_window(self):
        document = mcs.build_document(ROWS[:3], MINUTE)
        calls = []

        class Connection:
            def execute(self, sql, params=()):
                calls.append((sql, params))
                return SimpleNamespace(fetchone=lambda: {"effective_at": MINUTE, "normalized": json.dumps(document)},
                                       fetchall=lambda: [{"effective_at": MINUTE, "normalized": document}])

        observed, loaded = mcs.latest(Connection(), date(2026, 10, 9))
        self.assertEqual((observed, mcs.rows_of(loaded)), (MINUTE, ROWS[:3]))
        self.assertIn("DESC", calls[-1][0])
        mcs.first_between(Connection(), MINUTE, MINUTE + timedelta(minutes=5))
        self.assertIn("ASC", calls[-1][0])
        self.assertEqual(len(mcs.day_documents(Connection(), date(2026, 10, 9))), 1)


class MemoryTests(unittest.TestCase):
    def tearDown(self):
        mcs._LATEST.update(observed_at=None, rows=None)

    def test_the_newest_minute_is_served_while_fresh(self):
        now = datetime.now(timezone.utc)
        mcs.remember(now, ROWS[:2])
        self.assertEqual(mcs.recent(now + timedelta(seconds=60))[1], ROWS[:2])
        self.assertIsNone(mcs.recent(now + timedelta(seconds=200)))


class CaptureStorageTests(unittest.TestCase):
    def tearDown(self):
        mcs._LATEST.update(observed_at=None, rows=None)

    def _capture(self, storage, *, document_fails=False):
        from app.level1_snapshot_runtime import capture_level1_snapshot
        persisted, documents = [], []

        async def fetch():
            return ROWS[:5], {"status": "fresh"}

        async def persist(provider, capability, rows):
            persisted.append(len(rows))
            return len(rows)

        async def persist_document(observed_at, rows, metadata):
            if document_fails:
                raise RuntimeError("pool exhausted")
            documents.append(len(rows))
            return {"rows": len(rows), "bytes": 100}

        async def session_open(_now):
            return True

        result = asyncio.run(capture_level1_snapshot(
            fetch_snapshot=fetch, persist=persist, session_open=session_open, persist_document=persist_document,
            storage=storage, now=datetime.now(timezone.utc)))
        return result, persisted, documents

    def test_each_storage_mode_writes_what_it_names(self):
        self.assertEqual(self._capture("per_symbol")[1:], ([5], []))
        self.assertEqual(self._capture("document")[1:], ([], [5]))
        self.assertEqual(self._capture("both")[1:], ([5], [5]))

    def test_a_failed_document_is_reported_beside_the_rows_and_fatal_when_it_is_the_only_copy(self):
        result, persisted, _ = self._capture("both", document_fails=True)
        self.assertEqual((result["status"], persisted), ("completed", [5]))
        self.assertIn("pool exhausted", result["document_error"])
        with self.assertRaises(RuntimeError):
            self._capture("document", document_fails=True)

    def test_the_capture_remembers_the_minute_for_the_latest_read(self):
        self._capture("per_symbol")
        self.assertEqual(len(mcs.recent()[1]), 5)

    def test_the_default_storage_is_both_until_the_owner_side_confirms(self):
        with patch.dict("os.environ", {}, clear=False):
            import os
            os.environ.pop("LEVEL1_STORAGE", None)
            self.assertEqual(mcs.storage_mode(), "both")
        with patch.dict("os.environ", {"LEVEL1_STORAGE": "document"}):
            self.assertEqual(mcs.storage_mode(), "document")
        with patch.dict("os.environ", {"LEVEL1_STORAGE": "nonsense"}):
            self.assertEqual(mcs.storage_mode(), "both")


class LatestReadTests(unittest.TestCase):
    def tearDown(self):
        mcs._LATEST.update(observed_at=None, rows=None)

    def test_the_latest_snapshot_reads_memory_then_the_document_then_the_rows(self):
        from app.market_result_read_model import latest_all_a_level1
        document = mcs.build_document(ROWS[:3], MINUTE)

        class Connection:
            def __init__(self, with_document):
                self.with_document = with_document

            def execute(self, sql, params=()):
                if "symbol IS NULL" in sql:
                    row = {"effective_at": MINUTE, "normalized": document} if self.with_document else None
                    return SimpleNamespace(fetchone=lambda: row)
                if "max(effective_at)" in sql:
                    return SimpleNamespace(fetchone=lambda: {"snapshot_at": MINUTE - timedelta(minutes=1)})
                if "count(*)" in sql:
                    return SimpleNamespace(fetchone=lambda: {"count": 1})
                return SimpleNamespace(fetchall=lambda: [{"symbol": "600001.SH", "normalized": ROWS[1]}])

        def database(with_document):
            connection = Connection(with_document)

            class Database:
                def transaction(self):
                    class Context:
                        def __enter__(self):
                            return connection

                        def __exit__(self, *exc):
                            return False
                    return Context()
            return Database()

        from_document = latest_all_a_level1(database(True), 10)
        self.assertEqual((from_document["source"], from_document["count"]), ("document", 3))
        self.assertEqual(from_document["items"][0]["normalized"]["capability"], "a_share_prices_snapshot")
        legacy = latest_all_a_level1(database(False), 10)
        self.assertEqual(legacy["count"], 1)
        mcs.remember(datetime.now(timezone.utc), ROWS[:2])
        self.assertEqual(latest_all_a_level1(database(True), 10)["source"], "memory")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
