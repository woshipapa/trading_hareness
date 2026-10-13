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

    def test_resume_session_order_and_which_empty_answers_are_kept(self):
        days = ["20260929", "20260930", "20261008", "20261009", "20261012"]
        calendar = Calendar({"2026-09-29", "2026-09-30", "2026-10-08", "2026-10-09", "2026-10-12"})
        with tempfile.TemporaryDirectory() as root:
            out = Path(root)
            (out / "AAA.jsonl").write_text(json.dumps({"day": "20261009"}) + "\n", encoding="utf-8")
            asked = []

            def call(code, day):
                asked.append((code, day))
                if day == "20260929":
                    time.sleep(0.02)   # the first session returns last: the file must still be in session order
                if code == "BBB" and day in ("20260929", "20261012"):
                    return {"trend": [], "errcode": 1020}
                return {"trend": [["09:31", day, 1, 2, 0]], "errcode": 0}

            MODULE.fetch(["AAA", "BBB"], days, out, workers=2, call=call, calendar=calendar, allow_session=True)
            self.assertNotIn(("AAA", "20261009"), asked, "a session already in the file is not asked again")
            stored = {code: [json.loads(line)["day"] for line in (out / f"{code}.jsonl").read_text(encoding="utf-8").splitlines()]
                      for code in ("AAA", "BBB")}
            self.assertEqual(stored["AAA"], ["20261009", "20260929", "20260930", "20261008", "20261012"])
            self.assertEqual(stored["BBB"], ["20260929", "20260930", "20261008", "20261009"],
                             "an old empty session is kept; an empty answer for one of the last sessions is not")
            asked.clear()
            MODULE.fetch(["AAA", "BBB"], days, out, workers=2, call=call, calendar=calendar, allow_session=True)
            self.assertEqual(asked, [("BBB", "20261012")], "only the recent session without data is asked again")

    def test_port_5681_is_refused(self):
        with self.assertRaisesRegex(ValueError, "local service"):
            MODULE.validate_base_url("http://127.0.0.1:5681")


if __name__ == "__main__":
    unittest.main()
