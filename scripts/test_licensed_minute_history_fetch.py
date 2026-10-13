import importlib.util
import json
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path


SCRIPT = Path(__file__).with_name("licensed_minute_history_fetch.py")
SPEC = importlib.util.spec_from_file_location("licensed_minute_history_fetch", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class Calendar:
    def __init__(self, sessions):
        self.sessions = sessions

    def is_session(self, value):
        return value.isoformat() in self.sessions

    def sessions_in_range(self, start, end):
        return [datetime.fromisoformat(value).date() for value in self.sessions if start <= value <= end]


class LicensedMinuteHistoryFetchTests(unittest.TestCase):
    def test_workers_are_bounded(self):
        self.assertEqual(MODULE.validate_workers(8), 8)
        with self.assertRaisesRegex(ValueError, "upstream refuses"):
            MODULE.validate_workers(9)

    def test_session_guard_uses_shanghai_exchange_date(self):
        calendar = Calendar({"2026-10-12"})
        self.assertTrue(MODULE.session_guard(datetime(2026, 10, 12, 2, 0, tzinfo=timezone.utc), calendar))
        self.assertFalse(MODULE.session_guard(datetime(2026, 10, 12, 8, 0, tzinfo=timezone.utc), calendar))
        self.assertFalse(MODULE.session_guard(datetime(2026, 10, 10, 2, 0, tzinfo=timezone.utc), calendar))

    def test_payload_classification(self):
        self.assertEqual(MODULE.classify_payload({"errcode": 1020, "trend": []}), "no data")
        self.assertEqual(MODULE.classify_payload({"errcode": 2001, "trend": []}), "refusal")

    def test_pending_days_and_session_order_with_out_of_order_returns(self):
        calendar = Calendar({"2026-10-09", "2026-10-12"})
        with tempfile.TemporaryDirectory() as root:
            out = Path(root)
            (out / "AAA.jsonl").write_text(json.dumps({"day": "20261009"}) + "\n", encoding="utf-8")
            asked = []
            returned = []

            def call(code, day):
                asked.append(day)
                if day == "20261009":
                    time.sleep(0.02)
                returned.append(day)
                return {"trend": [["09:31", day, 1, 2, 0]], "errcode": 0}

            MODULE.fetch(["AAA", "BBB"], ["20261009", "20261012"], out, workers=2, call=call, calendar=calendar,
                         allow_session=True)
            self.assertEqual(sorted(asked), ["20261009", "20261012", "20261012"])
            self.assertEqual(returned[:2], ["20261012", "20261012"])
            self.assertEqual(returned[2], "20261009")
            rows = [json.loads(line) for line in (out / "AAA.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertEqual([row["day"] for row in rows], ["20261009", "20261012"])
            rows = [json.loads(line) for line in (out / "BBB.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertEqual([row["day"] for row in rows], ["20261009", "20261012"])

    def test_port_5681_is_refused(self):
        with self.assertRaisesRegex(ValueError, "local service"):
            MODULE.validate_base_url("http://127.0.0.1:5681")


if __name__ == "__main__":
    unittest.main()
