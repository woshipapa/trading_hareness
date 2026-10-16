from __future__ import annotations

import json
import hashlib
import asyncio
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from app.intraday_evidence_spool import LocalEvidenceSpool, SpoolConfig, SpoolCorrupt, replay_pending, shared_spool


def observation(payload: object = None) -> dict:
    body = payload or {"rows": {"000001.SZ": {"p": 1}}}
    return {
        "schema_version": "evidence-record-v1", "target_stream": "raw_market_observations",
        "batch_id": "00000000-0000-0000-0000-000000000001", "sequence": 0,
        "captured_at": "2026-10-16T07:30:00+00:00",
        "provider_key": "quant_scan", "capability": "watch_scan_tape", "market": "cn",
        "symbol": "watch:scan", "effective_at": "2026-10-16T07:30:00+00:00",
        "available_at": "2026-10-16T07:30:00+00:00",
        "payload_sha256": hashlib.sha256(json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
        "normalized": {"stocks": 1}, "payload": body,
    }


class _Cursor:
    def __init__(self, calls: list):
        self.calls = calls

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def executemany(self, sql, rows):
        self.calls.append((sql, list(rows)))


class _Database:
    def __init__(self):
        self.calls = []

    def transaction(self):
        database = self

        class Tx:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def cursor(self):
                return _Cursor(database.calls)

        return Tx()


class IntradayEvidenceSpoolTests(unittest.TestCase):
    def test_write_is_atomic_and_memory_cache_is_bounded(self):
        with tempfile.TemporaryDirectory() as directory:
            spool = LocalEvidenceSpool(SpoolConfig(Path(directory), memory_items=1, min_free_bytes=1))
            first = spool.write(observation())
            second = spool.write(observation({"rows": {"000002.SZ": {"p": 2}}}))
            self.assertNotEqual(first, second)
            paths = spool.pending("2026-10-16")
            self.assertEqual(len(paths), 1)
            self.assertEqual(len(spool.read_segment(paths[0])), 2)
            self.assertFalse(any(path.name.endswith(".tmp") for path in paths))
            self.assertEqual(len(spool._memory), 1)

    def test_memory_cache_coalesces_a_retry_of_the_same_envelope(self):
        with tempfile.TemporaryDirectory() as directory:
            spool = LocalEvidenceSpool(SpoolConfig(Path(directory), min_free_bytes=1))
            first = spool.write(observation())
            second = spool.write(observation())
            self.assertEqual(first, second)
            self.assertEqual(len(spool.pending()), 1)

    def test_tampered_envelope_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            spool = LocalEvidenceSpool(SpoolConfig(Path(directory), min_free_bytes=1))
            spool.write(observation())
            path = spool.pending()[0]
            lines = path.read_text().splitlines()
            data = json.loads(lines[1])
            data["observation"]["payload"]["rows"]["000001.SZ"]["p"] = 9
            lines[1] = json.dumps(data)
            path.write_text("\n".join(lines) + "\n")
            with self.assertRaises(SpoolCorrupt):
                spool.read(path)

    def test_replay_is_batch_idempotent_and_archives_files(self):
        with tempfile.TemporaryDirectory() as directory:
            config = SpoolConfig(Path(directory), min_free_bytes=1)
            spool = LocalEvidenceSpool(config)
            spool.write(observation())
            database = _Database()
            result = replay_pending(database, config=config, trade_date="2026-10-16")
            self.assertEqual(result["status"], "completed")
            self.assertEqual(result["rows"], 1)
            self.assertEqual(len(database.calls), 1)
            self.assertEqual(spool.pending(), [])
            self.assertEqual(len(list((Path(directory) / "done").rglob("*.jsonl"))), 1)
            again = replay_pending(database, config=config, trade_date="2026-10-16")
            self.assertEqual(again["status"], "empty")

    def test_cleanup_done_respects_recovery_window(self):
        with tempfile.TemporaryDirectory() as directory:
            config = SpoolConfig(Path(directory), min_free_bytes=1, done_retention_days=1)
            spool = LocalEvidenceSpool(config)
            spool.write(observation())
            database = _Database()
            replay_pending(database, config=config)
            done = next((Path(directory) / "done").rglob("*.jsonl"))
            done.touch()
            self.assertEqual(spool.cleanup_done(now=time.time() + 2 * 86400), 1)
            self.assertFalse(done.exists())

    def test_watch_scan_tape_routes_to_spool_only_when_enabled(self):
        from app.watch_scan_tape import persist_scan_tape

        with tempfile.TemporaryDirectory() as directory:
            config = SpoolConfig(Path(directory), min_free_bytes=1)
            with patch("app.intraday_evidence_spool.SpoolConfig.from_settings", return_value=config), \
                 patch.dict("os.environ", {"INTRADAY_EVIDENCE_SPOOL_ENABLED": "true"}, clear=False):
                self.assertTrue(persist_scan_tape(object(), scan_id="scan-1", observed_at=__import__("datetime").datetime(2026, 10, 16, tzinfo=__import__("datetime").timezone.utc), rows={"000001.SZ": {"p": 1}}))
            self.assertEqual(len(LocalEvidenceSpool(config).pending()), 1)

    def test_shared_writer_coalesces_records_until_segment_boundary(self):
        with tempfile.TemporaryDirectory() as directory:
            config = SpoolConfig(Path(directory), min_free_bytes=1, segment_max_age_seconds=60)
            writer = shared_spool(config)
            writer.write(observation())
            writer.write(observation({"rows": {"000002.SZ": {"p": 2}}}))
            self.assertEqual(len(writer.pending()), 1)
            self.assertEqual(len(writer.read_segment(writer.pending()[0])), 2)

    def test_watch_scan_tape_falls_back_to_database_when_ssd_is_full(self):
        from app.watch_scan_tape import persist_scan_tape

        class Connection:
            def __init__(self):
                self.calls = 0

            def execute(self, *_args):
                self.calls += 1

                class Row:
                    def fetchone(self):
                        return {"observation_id": "1"}

                return Row()

        connection = Connection()
        with patch.dict("os.environ", {"INTRADAY_EVIDENCE_SPOOL_ENABLED": "true"}, clear=False), \
             patch("app.intraday_evidence_spool.LocalEvidenceSpool.write", side_effect=OSError("disk full")):
            self.assertTrue(persist_scan_tape(connection, scan_id="scan-2", observed_at=__import__("datetime").datetime(2026, 10, 16, tzinfo=__import__("datetime").timezone.utc), rows={"000001.SZ": {"p": 1}}))
        self.assertEqual(connection.calls, 1)

    def test_replay_limit_is_paced_by_the_configured_batch_size(self):
        with tempfile.TemporaryDirectory() as directory:
            config = SpoolConfig(Path(directory), min_free_bytes=1, max_batch=1, segment_max_records=1)
            spool = LocalEvidenceSpool(config)
            spool.write(observation())
            spool.write(observation({"rows": {"000002.SZ": {"p": 2}}}))
            database = _Database()
            result = replay_pending(database, config=config)
            self.assertEqual(result["rows"], 1)
            self.assertEqual(len(spool.pending()), 1)

    def test_replay_splits_a_large_segment_without_exceeding_row_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            config = SpoolConfig(Path(directory), min_free_bytes=1, max_batch=1, segment_max_records=10)
            spool = LocalEvidenceSpool(config)
            spool.write(observation())
            spool.write(observation({"rows": {"000002.SZ": {"p": 2}}}))
            database = _Database()
            first = replay_pending(database, config=config)
            self.assertEqual(first["rows"], 1)
            self.assertEqual(len(spool.pending()), 1)
            second = replay_pending(database, config=config)
            self.assertEqual(second["rows"], 1)
            self.assertEqual(spool.pending(), [])

    def test_paced_loop_uses_backoff_after_a_partial_pass(self):
        calls = []
        sleeps = []

        async def fake_run(*_args, **_kwargs):
            calls.append(1)
            if len(calls) == 1:
                return {"status": "partial"}
            raise asyncio.CancelledError

        async def fake_sleep(value):
            sleeps.append(value)

        from app.intraday_evidence_spool import run_paced_replay_loop
        config = SpoolConfig(Path(tempfile.gettempdir()), replay_interval_seconds=5, replay_backoff_seconds=11)
        with self.assertRaises(asyncio.CancelledError):
            asyncio.run(run_paced_replay_loop(object(), fake_run, config=config, sleep=fake_sleep))
        self.assertEqual(sleeps, [11])


if __name__ == "__main__":
    unittest.main()
