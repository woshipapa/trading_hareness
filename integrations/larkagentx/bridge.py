"""Small, token-protected HTTP bridge around the pulled LarkAgentX client.

The bridge deliberately sends inbound messages to the n8n Feishu adapter first.
That keeps the adapter's durable idempotency and downstream delivery contract in
place.  It exposes text sending separately because LarkAgentX currently has no
media/card sender or remote receipt API.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from larkx.auth import AuthExpired, LarkAuth
from larkx.client import LarkClient


LOG = logging.getLogger("larkagentx-bridge")
MAX_BODY_BYTES = 64 * 1024


def csv_env(name: str) -> set[str]:
	return {part.strip() for part in os.environ.get(name, "").split(",") if part.strip()}


def required_env(name: str) -> str:
	value = os.environ.get(name, "").strip()
	if not value:
		raise RuntimeError(f"{name} must be configured")
	return value


def post_json(url: str, token: str, payload: dict[str, Any]) -> dict[str, Any]:
	data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
	request = Request(url, data=data, method="POST", headers={
		"content-type": "application/json",
		"x-larkagentx-token": token,
	})
	try:
		with urlopen(request, timeout=30) as response:
			body = response.read(MAX_BODY_BYTES).decode("utf-8", errors="replace")
		return json.loads(body) if body else {}
	except HTTPError as error:
		body = error.read(MAX_BODY_BYTES).decode("utf-8", errors="replace")
		raise RuntimeError(f"bridge HTTP {error.code}: {body[:240]}") from error
	except URLError as error:
		raise RuntimeError(f"bridge network error: {error.reason}") from error


class BridgeHandler(BaseHTTPRequestHandler):
	server_version = "LarkAgentXBridge/1"

	def log_message(self, format: str, *args: Any) -> None:
		LOG.info("%s - %s", self.address_string(), format % args)

	@property
	def bridge(self) -> "Bridge":
		return self.server.bridge  # type: ignore[attr-defined]

	def send_json(self, status: int, payload: dict[str, Any]) -> None:
		body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
		self.send_response(status)
		self.send_header("content-type", "application/json; charset=utf-8")
		self.send_header("cache-control", "no-store")
		self.send_header("content-length", str(len(body)))
		self.end_headers()
		self.wfile.write(body)

	def authorized(self) -> bool:
		return self.headers.get("x-larkagentx-token", "") == self.bridge.token

	def read_json(self) -> dict[str, Any]:
		length = int(self.headers.get("content-length", "0"))
		if length <= 0 or length > MAX_BODY_BYTES:
			raise ValueError("请求体为空或超过 64 KiB")
		value = json.loads(self.rfile.read(length).decode("utf-8"))
		if not isinstance(value, dict):
			raise ValueError("请求体必须是 JSON 对象")
		return value

	def do_GET(self) -> None:  # noqa: N802
		if self.path != "/health":
			self.send_json(404, {"status": "not_found"})
			return
		self.send_json(200, self.bridge.health())

	def do_POST(self) -> None:  # noqa: N802
		if not self.authorized():
			self.send_json(401, {"status": "unauthorized"})
			return
		try:
			payload = self.read_json()
			if self.path == "/send":
				result = self.bridge.send(payload)
				self.send_json(200, result)
				return
			self.send_json(404, {"status": "not_found"})
		except Exception as error:
			LOG.warning("send request failed: %s", error)
			self.send_json(400, {"status": "error", "message": str(error)[:240]})


class Bridge:
	def __init__(self) -> None:
		self.token = required_env("LARKX_BRIDGE_TOKEN")
		self.ingress_url = required_env("LARKX_INGRESS_URL")
		self.listen_chats = csv_env("LARKX_LISTEN_CHAT_IDS")
		self.send_chats = csv_env("LARKX_SEND_CHAT_IDS")
		if not self.listen_chats:
			raise RuntimeError("LARKX_LISTEN_CHAT_IDS must contain at least one chat ID")
		if not self.send_chats:
			raise RuntimeError("LARKX_SEND_CHAT_IDS must contain at least one chat ID")
		self.auth = LarkAuth()
		self.client = LarkClient(self.auth)

	def health(self) -> dict[str, Any]:
		return {
			"status": "ok",
			"component": "larkagentx-bridge",
			"listen_chat_count": len(self.listen_chats),
			"send_chat_count": len(self.send_chats),
			"user_id": self.auth.user_id or None,
		}

	def send(self, payload: dict[str, Any]) -> dict[str, Any]:
		chat_id = str(payload.get("chat_id", "")).strip()
		text = str(payload.get("text", "")).strip()
		root_id = str(payload.get("root_id", "")).strip() or None
		if chat_id not in self.send_chats:
			raise ValueError("目标会话不在 LARKX_SEND_CHAT_IDS 白名单中")
		if not text or len(text) > 10_000:
			raise ValueError("text 不能为空且不能超过 10000 字符")
		if root_id and len(root_id) > 128:
			raise ValueError("root_id 超过长度限制")
		if not self.client.send_msg(text, chat_id, root_id=root_id):
			raise RuntimeError("LarkAgentX 文本发送失败")
		return {"status": "accepted", "chat_id": chat_id, "root_id": root_id}

	async def on_message(self, message: dict[str, Any]) -> None:
		chat_id = str(message.get("chat_id", ""))
		if chat_id not in self.listen_chats:
			return
		if str(message.get("from_id", "")) == str(self.auth.user_id):
			return
		message_id = str(message.get("msg_id", "")).strip()
		if not message_id:
			LOG.warning("dropping LarkAgentX message without msg_id")
			return
		payload = dict(message)
		payload["source_label"] = os.environ.get("LARKX_SOURCE_LABEL", "LarkAgentX 个人会话")
		try:
			last_error: Exception | None = None
			for attempt in range(1, 4):
				try:
					result = await asyncio.to_thread(post_json, self.ingress_url, self.token, payload)
					LOG.info("inbound accepted message_id=%s status=%s job_id=%s", message_id, result.get("status"), result.get("job_id"))
					return
				except Exception as error:
					last_error = error
					if attempt < 3:
						await asyncio.sleep(attempt)
			raise last_error or RuntimeError("inbound delivery failed")
		except Exception as error:
			LOG.error("inbound delivery failed message_id=%s: %s", message_id, error)

	async def listen_forever(self) -> None:
		while True:
			try:
				await self.client.connect_websocket(self.on_message)
				LOG.warning("LarkAgentX websocket ended; reconnecting in 10 seconds")
			except AuthExpired:
				LOG.error("LarkAgentX credentials expired; run lark auth qr or lark auth import")
				raise
			except Exception as error:
				LOG.warning("LarkAgentX websocket failed: %s; reconnecting in 10 seconds", error)
			await asyncio.sleep(10)


def serve_http(bridge: Bridge) -> ThreadingHTTPServer:
	host = os.environ.get("LARKX_BRIDGE_HOST", "127.0.0.1")
	port = int(os.environ.get("LARKX_BRIDGE_PORT", "8090"))
	server = ThreadingHTTPServer((host, port), BridgeHandler)
	server.bridge = bridge  # type: ignore[attr-defined]
	threading.Thread(target=server.serve_forever, name="larkagentx-http", daemon=True).start()
	LOG.info("bridge HTTP listening on %s:%d", host, port)
	return server


def main() -> None:
	logging.basicConfig(level=os.environ.get("LARKX_LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(message)s")
	bridge = Bridge()
	server = serve_http(bridge)
	try:
		asyncio.run(bridge.listen_forever())
	finally:
		server.shutdown()


if __name__ == "__main__":
	main()
