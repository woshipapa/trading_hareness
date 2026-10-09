import sys
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import qr_login_service as qr


class FakeAuth:
    def __init__(self):
        self.user_id = ""
        self.cookies = {}


class FakeQr:
    """Stands in for larkx.auth.QrLogin without network or protobuf."""

    def __init__(self, *, token="qr-token", outcome=(True, {}), block=None, raise_on_wait=None):
        self.token = token
        self.qr_content = '{"qrlogin":{"token":"%s"}}' % token
        self._outcome = outcome
        self._block = block  # optional threading.Event the watcher waits on
        self._raise_on_wait = raise_on_wait
        self.finished_with = None

    def wait(self, timeout=180, interval=2, on_status=None):
        if self._raise_on_wait:
            raise self._raise_on_wait
        if on_status:
            on_status(2, "qr_login_polling")  # scanned, pending confirm
        if self._block is not None:
            self._block.wait(timeout=5)
        return self._outcome

    def finish(self, auth):
        self.finished_with = auth
        auth.user_id = "7551451231416434716"
        auth.cookies = {"session": "x"}
        return "ok"


def wait_until(predicate, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


class QrLoginServiceTests(unittest.TestCase):
    def make(self, qr_obj, **kwargs):
        self.auth = FakeAuth()
        self.reconnects = []
        return qr.QrLoginService(
            self.auth,
            qr_login_factory=lambda: qr_obj,
            on_login_success=lambda uid: self.reconnects.append(uid),
            png_renderer=lambda content: b"PNGBYTES",
            **kwargs,
        )

    def test_start_returns_token_and_rendered_png(self):
        service = self.make(FakeQr(block=threading.Event()))
        started = service.start()
        self.assertEqual(started["status"], "waiting")
        self.assertEqual(started["token"], "qr-token")
        self.assertTrue(started["qr_png"].startswith("data:image/png;base64,"))
        self.assertIn("session_id", started)

    def test_start_without_renderer_falls_back_to_token_only(self):
        self.auth = FakeAuth()
        self.reconnects = []
        service = qr.QrLoginService(self.auth, qr_login_factory=lambda: FakeQr(block=threading.Event()),
                                    on_login_success=lambda uid: self.reconnects.append(uid),
                                    png_renderer=lambda content: None)
        started = service.start()
        self.assertNotIn("qr_png", started)
        self.assertEqual(started["qr_content"], '{"qrlogin":{"token":"qr-token"}}')

    def test_successful_scan_persists_credentials_and_fires_reconnect(self):
        service = self.make(FakeQr(outcome=(True, {})))
        started = service.start()
        sid = started["session_id"]
        self.assertTrue(wait_until(lambda: service.poll(sid)["logged_in"]))
        result = service.poll(sid)
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["user_id"], "7551451231416434716")
        self.assertEqual(self.reconnects, ["7551451231416434716"])
        self.assertEqual(self.auth.cookies, {"session": "x"})
        self.assertEqual(service.stats()["login_count"], 1)

    def test_cancelled_scan_reports_failed_and_never_reconnects(self):
        service = self.make(FakeQr(outcome=(False, {})))
        sid = service.start()["session_id"]

        def failed():
            with service._lock:
                service._sessions[sid].status = 3
                service._sessions[sid].status_name = qr.QR_STATUS_NAMES[3]
            return True

        failed()
        self.assertTrue(wait_until(lambda: not service._sessions[sid].thread.is_alive()))
        result = service.poll(sid)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(self.reconnects, [])

    def test_wait_exception_marks_session_error(self):
        service = self.make(FakeQr(raise_on_wait=RuntimeError("boom")))
        sid = service.start()["session_id"]
        self.assertTrue(wait_until(lambda: service.poll(sid)["status"] == "failed"))
        self.assertIn("boom", service.poll(sid).get("message", ""))
        self.assertEqual(self.reconnects, [])

    def test_init_failure_is_reported_not_raised(self):
        self.auth = FakeAuth()
        self.reconnects = []

        def boom():
            raise RuntimeError("qr init 失败")

        service = qr.QrLoginService(self.auth, qr_login_factory=boom)
        result = service.start()
        self.assertEqual(result["status"], "error")
        self.assertIn("二维码初始化失败", result["message"])

    def test_max_sessions_is_bounded(self):
        service = self.make(FakeQr(block=threading.Event()), max_sessions=2)
        a, b = service.start(), service.start()
        self.assertEqual(a["status"], "waiting")
        self.assertEqual(b["status"], "waiting")
        third = service.start()
        self.assertEqual(third["status"], "busy")

    def test_poll_unknown_session(self):
        service = self.make(FakeQr(block=threading.Event()))
        self.assertEqual(service.poll("nope")["status"], "unknown")

    def test_finalize_is_idempotent(self):
        service = self.make(FakeQr(outcome=(True, {})))
        sid = service.start()["session_id"]
        self.assertTrue(wait_until(lambda: service.poll(sid)["logged_in"]))
        service._finalize(sid)  # a double call must not double-count or re-reconnect
        self.assertEqual(service.stats()["login_count"], 1)
        self.assertEqual(self.reconnects, ["7551451231416434716"])


if __name__ == "__main__":
    unittest.main()
