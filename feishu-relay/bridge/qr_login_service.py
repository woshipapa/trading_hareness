"""Web-friendly QR login orchestration for the LarkAgentX personal session.

Upstream ``larkx.auth.QrLogin`` speaks the Feishu accounts QR protocol
(init → polling → finish) but only as a one-shot CLI that writes a PNG. This
wraps it as a small, bounded, thread-safe session manager the bridge's HTTP
server can drive: start a login (returns the QR as a ``data:`` PNG), poll its
status, and on a successful scan persist the cookies into the shared
``LarkAuth`` and fire a callback so the live client (re)connects — a human
scan becomes the only manual step, and it works whether the current session is
merely aging or already dead.

Everything upstream- and render-specific is injected, so the orchestration is
unit-testable on a machine without the vendored larkx package, the ``qrcode``
dependency, or network access.

No credential value is ever logged or returned to a caller: only status names
and the (public, short-lived) QR token leave this module.
"""

from __future__ import annotations

import base64
import logging
import secrets
import threading
import time
from typing import Any, Callable

LOG = logging.getLogger("larkagentx-bridge")

# Upstream larkx.auth.QR_STATUS_NAMES, duplicated so a caller gets a stable
# name without importing the edge-only package. 0 SUCCESS / 1 waiting-scan /
# 2 scanned-pending-confirm / 3 cancelled / 4 error / 5 expired.
QR_STATUS_NAMES = {0: "success", 1: "waiting_scan", 2: "scanned_confirm",
                   3: "cancelled", 4: "error", 5: "expired"}
TERMINAL_FAILURE_STATUSES = {3, 4, 5}

DEFAULT_MAX_SESSIONS = 4
DEFAULT_SESSION_TTL_SECONDS = 180


def default_png_renderer(content: str) -> bytes | None:
    """Render a QR payload to PNG bytes, or None when ``qrcode`` is absent."""
    try:
        import io

        import qrcode  # type: ignore[import-not-found]
    except Exception:  # noqa: BLE001 - optional dependency; page can render client-side
        return None
    code = qrcode.QRCode(border=1)
    code.add_data(content)
    code.make(fit=True)
    buffer = io.BytesIO()
    code.make_image().save(buffer, format="PNG")
    return buffer.getvalue()


class _Session:
    __slots__ = ("qr", "created_at", "status", "status_name", "logged_in",
                 "error", "user_id", "finalized", "thread")

    def __init__(self, qr: Any) -> None:
        self.qr = qr
        self.created_at = time.time()
        self.status = 1
        self.status_name = QR_STATUS_NAMES[1]
        self.logged_in = False
        self.error: str | None = None
        self.user_id: str | None = None
        self.finalized = False
        self.thread: threading.Thread | None = None


class QrLoginService:
    """Bounded, thread-safe manager for in-flight QR login sessions."""

    def __init__(
        self,
        auth: Any,
        *,
        qr_login_factory: Callable[[], Any],
        on_login_success: Callable[[str], None] | None = None,
        png_renderer: Callable[[str], bytes | None] = default_png_renderer,
        max_sessions: int = DEFAULT_MAX_SESSIONS,
        session_ttl_seconds: int = DEFAULT_SESSION_TTL_SECONDS,
    ) -> None:
        self._auth = auth
        self._qr_login_factory = qr_login_factory
        self._on_login_success = on_login_success
        self._png_renderer = png_renderer
        self._max_sessions = max(1, int(max_sessions))
        self._ttl = max(30, int(session_ttl_seconds))
        self._sessions: dict[str, _Session] = {}
        self._lock = threading.Lock()
        self.login_count = 0
        self.last_login_at: float | None = None
        self.last_login_user_id: str | None = None

    def _evict_expired_locked(self) -> None:
        cutoff = time.time() - self._ttl
        stale = [sid for sid, session in self._sessions.items()
                 if session.created_at < cutoff and not session.logged_in]
        for sid in stale:
            self._sessions.pop(sid, None)

    def start(self) -> dict[str, Any]:
        """Begin a login: open an upstream QR session and watch it in a thread."""
        with self._lock:
            self._evict_expired_locked()
            if len(self._sessions) >= self._max_sessions:
                return {"status": "busy",
                        "message": f"已有 {len(self._sessions)} 个登录会话在进行,请稍后再试"}
        try:
            qr = self._qr_login_factory()
        except Exception as error:  # noqa: BLE001 - network/protocol failures are reported, not raised
            LOG.warning("QR 登录初始化失败: %s", error)
            return {"status": "error", "message": f"二维码初始化失败: {str(error)[:200]}"}

        session = _Session(qr)
        session_id = secrets.token_urlsafe(18)
        token = str(getattr(qr, "token", "") or "")
        qr_content = str(getattr(qr, "qr_content", "") or token)
        png = None
        try:
            png = self._png_renderer(qr_content)
        except Exception as error:  # noqa: BLE001 - rendering must never break the flow
            LOG.warning("QR 渲染失败,回退到页面端渲染: %s", error)

        with self._lock:
            self._sessions[session_id] = session
            thread = threading.Thread(target=self._watch, args=(session_id,),
                                      name=f"qr-login-{session_id[:6]}", daemon=True)
            session.thread = thread
            thread.start()

        result: dict[str, Any] = {
            "status": "waiting",
            "session_id": session_id,
            "status_name": session.status_name,
            "token": token,
            "qr_content": qr_content,
            "expires_in": self._ttl,
        }
        if png:
            result["qr_png"] = "data:image/png;base64," + base64.b64encode(png).decode("ascii")
        return result

    def _watch(self, session_id: str) -> None:
        session = self._sessions.get(session_id)
        if session is None:
            return

        def on_status(status: Any, _step: Any) -> None:
            try:
                numeric = int(status)
            except (TypeError, ValueError):
                return
            with self._lock:
                session.status = numeric
                session.status_name = QR_STATUS_NAMES.get(numeric, str(numeric))

        try:
            ok, _data = session.qr.wait(timeout=self._ttl, interval=2, on_status=on_status)
        except Exception as error:  # noqa: BLE001 - a failed poll ends this session, not the service
            with self._lock:
                session.status, session.status_name = 4, QR_STATUS_NAMES[4]
                session.error = str(error)[:200]
            LOG.warning("QR 登录轮询失败 session=%s: %s", session_id[:6], error)
            return

        if not ok:
            with self._lock:
                if session.status not in TERMINAL_FAILURE_STATUSES:
                    session.status, session.status_name = 5, QR_STATUS_NAMES[5]
            return
        self._finalize(session_id)

    def _finalize(self, session_id: str) -> None:
        session = self._sessions.get(session_id)
        if session is None:
            return
        with self._lock:
            if session.finalized:
                return
            session.finalized = True
        try:
            session.qr.finish(self._auth)
        except Exception as error:  # noqa: BLE001 - surfaced as session error, credentials untouched
            with self._lock:
                session.status, session.status_name = 4, QR_STATUS_NAMES[4]
                session.error = str(error)[:200]
            LOG.warning("QR 登录完成阶段失败 session=%s: %s", session_id[:6], error)
            return
        user_id = str(getattr(self._auth, "user_id", "") or "")
        with self._lock:
            session.status, session.status_name = 0, QR_STATUS_NAMES[0]
            session.logged_in = True
            session.user_id = user_id
            self.login_count += 1
            self.last_login_at = time.time()
            self.last_login_user_id = user_id
        LOG.info("QR 登录成功 user_id=%s,触发客户端重连", user_id or "?")
        if self._on_login_success:
            try:
                self._on_login_success(user_id)
            except Exception as error:  # noqa: BLE001 - reconnect failure must not unwind the login
                LOG.warning("QR 登录成功后的重连回调失败: %s", error)

    def poll(self, session_id: str) -> dict[str, Any]:
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                return {"status": "unknown", "message": "登录会话不存在或已过期"}
            payload = {
                "status": "success" if session.logged_in else (
                    "failed" if session.status in TERMINAL_FAILURE_STATUSES else "waiting"),
                "status_name": session.status_name,
                "logged_in": session.logged_in,
            }
            if session.error:
                payload["message"] = session.error
            if session.logged_in and session.user_id:
                payload["user_id"] = session.user_id
            return payload

    def stats(self) -> dict[str, Any]:
        with self._lock:
            return {
                "active_sessions": len(self._sessions),
                "login_count": self.login_count,
                "last_login_at": self.last_login_at,
                "last_login_user_id": self.last_login_user_id,
            }
