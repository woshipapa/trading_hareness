#!/usr/bin/env python3
"""Serve the generated service index at http://127.0.0.1:8800/.

A ten-line static host so the launchpad has a memorable, always-on URL
instead of a file:// path, and so the page's same-origin JavaScript may probe
the other loopback ports for liveness dots. It reads docs/services.html on
every request, so regenerating the page needs no restart. It also serves the
generated pages listed in GENERATED (the B300 collaboration progress page at
/b300, written by scripts/b300_collab_dashboard.py). Loopback only; no secrets.
"""

from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / "docs" / "services.html"
# Generated pages under state/ (gitignored), refreshed by their own jobs; read on every request.
GENERATED = {
    "/b300": (ROOT / "state" / "b300-collab-dashboard.html", "text/html; charset=utf-8"),
    "/b300.json": (ROOT / "state" / "b300-collab-dashboard.json", "application/json; charset=utf-8"),
}
HOST = os.environ.get("SERVICE_INDEX_HOST", "127.0.0.1")
PORT = int(os.environ.get("SERVICE_INDEX_PORT", "8888"))


class Handler(BaseHTTPRequestHandler):
    server_version = "service-index/1"

    def log_message(self, *_args):
        return

    def _send(self, status, data, content_type):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path in {"/", "/services.html", "/index.html"}:
            try:
                data = PAGE.read_bytes()
            except OSError:
                self._send(503, b'{"status":"page_missing"}', "application/json")
                return
            self._send(200, data, "text/html; charset=utf-8")
            return
        if path in GENERATED:
            source, content_type = GENERATED[path]
            try:
                data = source.read_bytes()
            except OSError:
                self._send(503, b'{"status":"page_missing"}', "application/json")
                return
            self._send(200, data, content_type)
            return
        if path == "/health":
            self._send(200, json.dumps({"status": "ok", "page": PAGE.exists()}).encode(),
                       "application/json")
            return
        self._send(404, b'{"status":"not_found"}', "application/json")


def main():
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
