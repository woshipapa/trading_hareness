from __future__ import annotations

import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastapi.responses import Response

from app.routers.system_control import (
    HEALTH_DEADLINE_SECONDS,
    SystemControlDependencies,
    build_system_control_router,
)


class _Unavailable(RuntimeError):
    pass


class SystemControlRouterTests(unittest.TestCase):
    def test_default_fallback_precedes_container_socket_deadline(self) -> None:
        self.assertLess(HEALTH_DEADLINE_SECONDS, 3.0)

    def _client(self, *, health_payload=lambda: {"status": "ok"}, bootstrap=lambda: {"status": "ok"}) -> TestClient:
        app = FastAPI()
        app.include_router(build_system_control_router(SystemControlDependencies(
            health_payload=health_payload,
            database_unavailable_error=_Unavailable,
            metrics_response=lambda: Response(b"quant_test_metric 1\n", media_type="text/plain"),
            legacy_bootstrap=bootstrap,
        )))
        return TestClient(app)

    def test_operational_routes_preserve_urls_and_local_response_contracts(self) -> None:
        with self._client(bootstrap=lambda: {"status": "ok", "catalog": {"apis": 1}}) as client:
            self.assertEqual(client.get("/health").json(), {"status": "ok"})
            metrics = client.get("/metrics")
            self.assertEqual(metrics.status_code, 200)
            self.assertIn("quant_test_metric 1", metrics.text)
            self.assertEqual(client.post("/api/v1/bootstrap").json(), {"status": "ok", "catalog": {"apis": 1}})

    def test_database_unavailable_is_a_strict_health_failure(self) -> None:
        def unavailable() -> dict[str, object]:
            raise _Unavailable("pool closed")

        with self._client(health_payload=unavailable) as client:
            response = client.get("/health")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["detail"], "database unavailable: pool closed")


class BusyHealthTests(unittest.TestCase):
    """A slow probe answers from memory; a wedged service still fails."""

    def _client(self, health_payload, *, clock=None, deadline=0.05, grace=180.0):
        from app.routers.system_control import BusyTracker

        now = [0.0]
        tracker = BusyTracker(clock=clock or (lambda: now[0]))
        app = FastAPI()
        app.include_router(build_system_control_router(SystemControlDependencies(
            health_payload=health_payload, database_unavailable_error=_Unavailable,
            metrics_response=lambda: Response(b"", media_type="text/plain"),
            legacy_bootstrap=lambda: {"status": "ok"},
            busy_payload=lambda: {"service": "quant-research", "database_pool": {"available": 0, "waiting": 9}},
            health_deadline_seconds=deadline, busy_grace_seconds=grace, busy_tracker=tracker,
        )))
        return TestClient(app), now

    def test_a_slow_probe_answers_busy_with_200_instead_of_going_silent(self) -> None:
        import threading
        release = threading.Event()
        client, _now = self._client(lambda: (release.wait(2), {"status": "ok"})[1])
        with client:
            response = client.get("/health")
            release.set()
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "busy")
        self.assertEqual(body["database_pool"]["waiting"], 9)
        self.assertIn("answered from memory", body["busy_reason"])

    def test_a_service_busy_beyond_the_grace_window_still_fails(self) -> None:
        import threading
        release = threading.Event()
        client, now = self._client(lambda: (release.wait(2), {"status": "ok"})[1], grace=60.0)
        with client:
            self.assertEqual(client.get("/health").status_code, 200)   # busy starts at t=0
            now[0] = 61.0
            response = client.get("/health")
            release.set()
        self.assertEqual(response.status_code, 503)
        self.assertIn("has not completed", response.json()["detail"])

    def test_a_full_answer_resets_the_busy_clock(self) -> None:
        import threading
        slow = threading.Event()
        state = {"slow": True}

        def payload():
            if state["slow"]:
                slow.wait(2)
            return {"status": "ok"}

        client, now = self._client(payload, grace=60.0)
        with client:
            self.assertEqual(client.get("/health").json()["status"], "busy")
            slow.set()
            state["slow"] = False
            now[0] = 30.0
            # The first slow computation is still in flight and shared, so wait for it to land.
            for _ in range(50):
                if client.get("/health").json().get("status") == "ok":
                    break
            now[0] = 200.0
            self.assertEqual(client.get("/health").json()["status"], "ok")

    def test_an_unreachable_database_still_fails_at_once(self) -> None:
        def unavailable() -> dict[str, object]:
            raise _Unavailable("connection refused")

        client, _now = self._client(unavailable)
        with client:
            response = client.get("/health")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["detail"], "database unavailable: connection refused")
