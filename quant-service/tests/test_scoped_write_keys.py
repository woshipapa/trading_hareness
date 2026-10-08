"""Named, scoped write keys, with the single legacy key still accepted."""

from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.security import (
    WRITE_CALLERS, authorize_write, raw_overflow_archive_allowed, write_boundary_status, write_credentials,
)

RELAY_KEY = "r" * 32
TEACHER_KEY = "t" * 32
LEGACY_KEY = "legacy-key-0123456789abcdef"
SCOPED = f"relay|/api/v1/ingestion/,/api/v1/internal/raw-overflow/|{RELAY_KEY};teacher|/api/v1/teacher-review/|{TEACHER_KEY}"


def _request(path: str, key: str | None = None, method: str = "POST"):
    headers = {"X-Quant-Write-Key": key} if key else {}
    return SimpleNamespace(method=method, url=SimpleNamespace(path=path), headers=headers)


class WriteCredentialParsingTests(unittest.TestCase):
    def test_legacy_and_scoped_keys_are_both_credentials(self):
        credentials, problems = write_credentials({"QUANT_WRITE_API_KEY": LEGACY_KEY, "QUANT_WRITE_API_KEYS": SCOPED})
        self.assertEqual(problems, ())
        self.assertEqual([(c.caller, c.scopes) for c in credentials], [
            ("legacy", ("*",)),
            ("relay", ("/api/v1/ingestion/", "/api/v1/internal/raw-overflow/")),
            ("teacher", ("/api/v1/teacher-review/",)),
        ])

    def test_malformed_entries_are_reported_without_their_values_and_ignored(self):
        bad = ";".join([
            "no-separators-at-all",
            "BadName|/api/v1/x/|" + "a" * 30,
            "relay|not-a-path|" + "b" * 30,
            "short|/api/v1/x/|tiny",
            f"relay|/api/v1/x/|{RELAY_KEY}",
            f"again|/api/v1/y/|{RELAY_KEY}",
        ])
        credentials, problems = write_credentials({"QUANT_WRITE_API_KEYS": bad})
        self.assertEqual([c.caller for c in credentials], ["relay"])
        self.assertEqual(len(problems), 5)
        joined = " ".join(problems)
        for secret in (RELAY_KEY, "a" * 30, "b" * 30, "tiny"):
            self.assertNotIn(secret, joined, "a problem message must never carry a key")

    def test_the_legacy_name_cannot_be_claimed(self):
        _, problems = write_credentials({"QUANT_WRITE_API_KEYS": f"legacy|*|{RELAY_KEY}"})
        self.assertTrue(problems)

    def test_status_shows_callers_and_scopes_but_no_keys(self):
        status = write_boundary_status({"QUANT_WRITE_API_KEY": LEGACY_KEY, "QUANT_WRITE_API_KEYS": SCOPED})
        self.assertEqual([item["caller"] for item in status["callers"]], ["legacy", "relay", "teacher"])
        self.assertNotIn(RELAY_KEY, repr(status))
        self.assertNotIn(LEGACY_KEY, repr(status))


class AuthorizeWriteTests(unittest.TestCase):
    def setUp(self):
        self.credentials, _ = write_credentials({"QUANT_WRITE_API_KEY": LEGACY_KEY, "QUANT_WRITE_API_KEYS": SCOPED})

    def test_reads_need_no_key(self):
        self.assertTrue(authorize_write("GET", "/api/v1/anything", None, self.credentials).allowed)

    def test_no_configured_keys_keeps_the_old_open_behaviour(self):
        self.assertTrue(authorize_write("POST", "/api/v1/anything", None, ()).allowed)

    def test_an_unknown_key_is_401(self):
        decision = authorize_write("POST", "/api/v1/ingestion/jobs", "nope" * 8, self.credentials)
        self.assertEqual((decision.allowed, decision.status), (False, 401))
        self.assertEqual(decision.reason, "valid X-Quant-Write-Key is required for write operations")

    def test_a_known_key_outside_its_scope_is_403_and_names_the_caller(self):
        decision = authorize_write("POST", "/api/v1/intraday/watchlists/000001.SZ", RELAY_KEY, self.credentials)
        self.assertEqual((decision.allowed, decision.status, decision.caller), (False, 403, "relay"))

    def test_a_scoped_key_inside_its_scope_is_allowed_as_that_caller(self):
        decision = authorize_write("PUT", "/api/v1/teacher-review/packs", TEACHER_KEY, self.credentials)
        self.assertEqual((decision.allowed, decision.caller), (True, "teacher"))

    def test_the_legacy_key_still_writes_anywhere(self):
        decision = authorize_write("DELETE", "/api/v1/intraday/watchlists/000001.SZ", LEGACY_KEY, self.credentials)
        self.assertEqual((decision.allowed, decision.caller), (True, "legacy"))

    def test_the_raw_overflow_hand_off_honours_scopes_on_every_verb(self):
        path = "/api/v1/internal/raw-overflow/next"
        self.assertTrue(raw_overflow_archive_allowed(_request(path, RELAY_KEY, "GET"), self.credentials))
        self.assertFalse(raw_overflow_archive_allowed(_request(path, TEACHER_KEY, "GET"), self.credentials))
        self.assertTrue(raw_overflow_archive_allowed(_request(path, LEGACY_KEY, "GET"), LEGACY_KEY))


class WriteMiddlewareTests(unittest.TestCase):
    """The real middleware, on a path that is refused or 404s before any database work."""

    def test_401_403_and_pass_through(self):
        from fastapi.testclient import TestClient

        from app.main import app
        client = TestClient(app)
        env = {"QUANT_WRITE_API_KEY": LEGACY_KEY, "QUANT_WRITE_API_KEYS": SCOPED}
        before = WRITE_CALLERS.snapshot()["writes_by_caller"].get("legacy", 0)
        with patch.dict(os.environ, env):
            unknown = client.post("/api/v1/teacher-review/nothing-here", headers={"X-Quant-Write-Key": "x" * 30})
            outside = client.post("/api/v1/intraday/nothing-here", headers={"X-Quant-Write-Key": TEACHER_KEY})
            legacy = client.post("/api/v1/intraday/nothing-here", headers={"X-Quant-Write-Key": LEGACY_KEY})
        self.assertEqual(unknown.status_code, 401)
        self.assertEqual(outside.status_code, 403)
        self.assertIn("teacher", outside.json()["detail"])
        self.assertEqual(legacy.status_code, 404, "an authorized write reaches routing")
        self.assertEqual(WRITE_CALLERS.snapshot()["writes_by_caller"]["legacy"], before + 1)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
