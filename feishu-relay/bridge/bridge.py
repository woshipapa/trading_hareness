"""Small, token-protected HTTP bridge around the pulled LarkAgentX client.

The bridge sends every allowlisted inbound event to the n8n Feishu adapter.
Text, complete cards, direct images and rich-text images whose protobuf
properties carry AES-GCM metadata can now stay on the private WebSocket path.
Other media remains fail-closed and may use the adapter's precise official
resource fallback.  The bridge exposes text sending separately because
LarkAgentX currently has no media/card sender or remote receipt API.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
from pathlib import Path
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit
from urllib.request import Request, urlopen

import requests
import websockets

from larkx.auth import AuthExpired, LarkAuth
from larkx.client import LarkClient
from larkx.proto import decoders
from larkagentx_image_property import extract_rich_text_image_resource
from proto_wire import decode_primary_websocket, tolerant_websocket_decode_with_meta
from event_spool import EventSpool
from history_archive import HistoryArchive
from owner_lock import OwnerLock, profile_storage_paths
from source_filter import DEFAULT_ANQIANG_BLOCK_KEYWORDS, matched_source_keyword, parse_csv


LOG = logging.getLogger("larkagentx-bridge")
MAX_BODY_BYTES = 64 * 1024
MAX_IMAGE_BYTES = 64 * 1024 * 1024
MAX_FORENSIC_FRAME_BYTES = 2 * 1024 * 1024
MAX_FORENSIC_FILES = 8
DEFAULT_SPOOL_REPLAY_SECONDS = 30
IMAGE_CDN_BASE_URL = "https://s1-imfile.feishucdn.com"
DEFAULT_GAP_REPAIR_SECONDS = 600
MIN_GAP_REPAIR_SECONDS = 120
MAX_GAP_REPAIR_SECONDS = 1800
DEFAULT_ROUTE_CATALOG_REFRESH_SECONDS = 15
DEFAULT_PRIVATE_REPAIR_MAX_POSITIONS = 64
MAX_PRIVATE_REPAIR_MAX_POSITIONS = 256
DEFAULT_PRIVATE_TAIL_REPAIR_SECONDS = 60
MIN_PRIVATE_TAIL_REPAIR_SECONDS = 30
MAX_PRIVATE_TAIL_REPAIR_SECONDS = 600
DEFAULT_PRIVATE_TAIL_REPAIR_WINDOW = 16


def bounded_int_env(name: str, default: int, minimum: int, maximum: int | None = None) -> int:
	try:
		value = int(os.environ.get(name, str(default)))
	except ValueError:
		value = default
	value = max(minimum, value)
	return min(maximum, value) if maximum is not None else value


class ProtocolForensics:
	"""Bounded, local-only capture of malformed WebSocket frames.

	The frame may contain private message material, so this is deliberately not
	logged or sent to the adapter.  It is only enabled for the exception path,
	kept under a 0700 directory, and rotated by count/size.
	"""

	def __init__(self, path: str | os.PathLike[str]) -> None:
		self.path = Path(path).expanduser()
		self.enabled = True
		try:
			self.path.mkdir(parents=True, exist_ok=True, mode=0o700)
			os.chmod(self.path, 0o700)
		except OSError as error:
			self.enabled = False
			LOG.warning("protocol forensic capture disabled: %s", error)

	def capture(self, raw: bytes, error: Exception) -> dict[str, Any] | None:
		if not self.enabled or not isinstance(raw, (bytes, bytearray)):
			return None
		if len(raw) > MAX_FORENSIC_FRAME_BYTES:
			return {"captured": False, "reason": "frame_too_large", "bytes": len(raw)}
		digest = hashlib.sha256(raw).hexdigest()
		stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
		base = self.path / f"{stamp}-{digest[:16]}"
		try:
			frame_path = base.with_suffix(".bin")
			meta_path = base.with_suffix(".json")
			frame_path.write_bytes(bytes(raw))
			os.chmod(frame_path, 0o600)
			meta_path.write_text(json.dumps({
				"captured_at": datetime.now(timezone.utc).isoformat(),
				"length": len(raw),
				"sha256": digest,
				"error": str(error)[:240],
				"error_type": type(error).__name__,
			}, ensure_ascii=False) + "\n", encoding="utf-8")
			os.chmod(meta_path, 0o600)
			files = sorted(self.path.glob("*.bin"), key=lambda item: item.stat().st_mtime, reverse=True)
			for old in files[MAX_FORENSIC_FILES:]:
				old.unlink(missing_ok=True)
				old.with_suffix(".json").unlink(missing_ok=True)
			return {"captured": True, "path": str(frame_path), "length": len(raw), "sha256": digest[:16]}
		except OSError as capture_error:
			LOG.warning("protocol forensic capture failed: %s", capture_error)
			return {"captured": False, "reason": "write_failed"}


class RecoveringLarkClient(LarkClient):
	"""Keep the upstream WebSocket client, but expose malformed-frame events.

	LarkAgentX currently logs and discards a protobuf frame when Feishu sends a
	new Card 2.0 variant.  The relay can then perform one bounded official
	history repair instead of silently losing the source message.  Normal
	WebSocket delivery remains unchanged.
	"""

	def __init__(self, auth: LarkAuth, on_decode_error=None, on_decode_fallback=None, on_connected=None):
		super().__init__(auth)
		self.on_decode_error = on_decode_error
		self.on_decode_fallback = on_decode_fallback
		self.on_connected = on_connected

	async def connect_websocket(self, on_message):
		self._ensure_loop()
		url = self.build_ws_url()
		async with websockets.connect(url) as ws:
			logging.getLogger("larkagentx-bridge").info("WS 已连接")
			if self.on_connected:
				try:
					self.on_connected()
				except Exception:
					pass
			heartbeat_task = asyncio.create_task(self.heartbeat_loop(ws))
			try:
				async for raw in ws:
					try:
						used_fallback = False
						proto_meta = None
						try:
							packet, messages = decode_primary_websocket(raw)
						except Exception as primary_error:
							packet, messages, proto_meta = tolerant_websocket_decode_with_meta(raw)
							used_fallback = True
							if self.on_decode_fallback:
								self.on_decode_fallback(primary_error, len(raw), len(messages), proto_meta)
						sid, cmd = (packet.get('sid'), packet.get('cmd'))
						if sid is not None:
							await self.send_ack(ws, sid)
						if cmd != 6:
							continue
						for msg in messages:
							if not msg.get('from_id'):
								continue
							asyncio.run_coroutine_threadsafe(self._dispatch(msg, on_message), self.loop)
					except Exception as error:
						if self.on_decode_error:
							try:
								self.on_decode_error(error, raw)
							except Exception:
								pass
						logging.getLogger("larkagentx-bridge").warning("跳过无法恢复的 WebSocket 帧 len=%d sha256=%s type=%s: %s", len(raw), hashlib.sha256(raw).hexdigest()[:16], type(error).__name__, error)
			finally:
				heartbeat_task.cancel()


def json_safe(value: Any) -> Any:
	"""Convert protobuf byte fields into JSON-safe text before HTTP delivery."""
	if isinstance(value, bytes):
		return value.decode("utf-8", errors="replace")
	if isinstance(value, dict):
		return {str(key): json_safe(item) for key, item in value.items()}
	if isinstance(value, (list, tuple)):
		return [json_safe(item) for item in value]
	return value


def _hex_bytes(value: Any) -> str:
	if isinstance(value, (bytes, bytearray)):
		return bytes(value).hex()
	if isinstance(value, str):
		return value.strip()
	return ""


def image_resource_info(message: dict[str, Any]) -> dict[str, str]:
	"""Extract the CDN image id and AES-GCM fields before bytes are JSON-safe."""
	data = message.get("content_data") or {}
	v2 = data.get("imageV2") or data.get("image_v2") or {}
	image_id = str(v2.get("imageKey") or v2.get("image_key") or "").strip()
	if not image_id:
		image = data.get("image") or {}
		origin = image.get("origin") or image.get("originImage") or {}
		image_id = str(origin.get("key") or origin.get("imageKey") or image.get("imageKey") or "").strip()
	crypto = v2.get("crypto") or {}
	cipher = crypto.get("cipher") or {}
	return {
		"image_id": image_id,
		"key_hex": _hex_bytes(cipher.get("key")),
		"iv_hex": _hex_bytes(cipher.get("iv")),
	}


def embedded_image_resources(message: dict[str, Any]) -> list[dict[str, str]]:
	"""Collect image keys carried inside POST/RichText protobuf dictionaries."""
	found: list[dict[str, str]] = []
	def add(item: dict[str, str]) -> None:
		if item.get("image_id") and item not in found:
			found.append(item)

	def visit(value: Any) -> None:
		if isinstance(value, dict):
			key = str(value.get("imageKey") or value.get("image_key") or "").strip()
			crypto = value.get("crypto") or {}
			cipher = crypto.get("cipher") or {}
			if key and ("image" in str(value.get("type", "")).lower() or cipher):
				item = {"image_id": key, "key_hex": _hex_bytes(cipher.get("key")), "iv_hex": _hex_bytes(cipher.get("iv"))}
				add(item)
			elements = value.get("elements")
			dictionary = elements.get("dictionary") if isinstance(elements, dict) else None
			if isinstance(dictionary, dict):
				for element_id, element in dictionary.items():
					if not isinstance(element, dict) or element.get("tag") not in (2, "IMG"):
						continue
					property_bytes = element.get("property")
					if isinstance(property_bytes, (bytes, bytearray)):
						add(extract_rich_text_image_resource(property_bytes, str(element_id)))
			for child in value.values(): visit(child)
		elif isinstance(value, (list, tuple)):
			for child in value: visit(child)
	visit(message.get("content_data") or {})
	return found


def image_download_url(image_id: str) -> str:
	return f"{IMAGE_CDN_BASE_URL}/static-resource/v1/{image_id}~?image_size=&cut_type=&quality=&format=&sticker_format=.webp"


def image_mime(data: bytes) -> str:
	if data.startswith(b"\xff\xd8\xff"):
		return "image/jpeg"
	if data.startswith(b"\x89PNG\r\n\x1a\n"):
		return "image/png"
	if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
		return "image/webp"
	if data.startswith((b"GIF87a", b"GIF89a")):
		return "image/gif"
	return "application/octet-stream"


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
		result = json.loads(body) if body else {}
		if isinstance(result, dict) and str(result.get("status", "")).strip().lower() == "failed":
			raise RuntimeError(f"adapter rejected event: {str(result.get('message') or result.get('error') or 'failed')[:240]}")
		return result
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
		if self.path == "/health":
			self.send_json(200, self.bridge.health())
			return
		if urlsplit(self.path).path == "/resource":
			if not self.authorized():
				self.send_json(401, {"status": "unauthorized"})
				return
			try:
				query = parse_qs(urlsplit(self.path).query)
				data, content_type = self.bridge.resource({key: values[0] for key, values in query.items() if values})
				self.send_response(200)
				self.send_header("content-type", content_type)
				self.send_header("content-length", str(len(data)))
				self.send_header("cache-control", "no-store")
				self.end_headers()
				self.wfile.write(data)
			except Exception as error:
				LOG.warning("image resource request failed: %s", error)
				self.send_json(400, {"status": "error", "message": str(error)[:240]})
			return
		if urlsplit(self.path).path in {"/history/export", "/history/status"}:
			if not self.authorized():
				self.send_json(401, {"status": "unauthorized"})
				return
			query = parse_qs(urlsplit(self.path).query)
			chat_id = str((query.get("chat_id") or [""])[0]).strip()
			if not chat_id or len(chat_id) > 128:
				self.send_json(400, {"status": "error", "message": "chat_id is required"})
				return
			if urlsplit(self.path).path == "/history/status":
				self.send_json(200, self.bridge.history_archive.stats(chat_id))
				return
			try:
				def number(name: str) -> float | None:
					value = str((query.get(name) or [""])[0]).strip()
					return float(value) if value else None
				rows = self.bridge.history_archive.export_rows(
					chat_id, from_time=number("from_time"), to_time=number("to_time"),
					after_sequence=int((query.get("after_sequence") or ["0"])[0] or 0),
					limit=int((query.get("limit") or ["10000"])[0] or 10000),
				)
				body = b"".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n" for row in rows)
				self.send_response(200)
				self.send_header("content-type", "application/x-ndjson; charset=utf-8")
				self.send_header("content-disposition", f'attachment; filename="larkagentx-{chat_id}-history.jsonl"')
				self.send_header("x-larkagentx-event-count", str(len(rows)))
				self.send_header("x-larkagentx-next-sequence", str(rows[-1]["sequence"] if rows else (self.bridge.history_archive.stats(chat_id)["latest_sequence"])))
				self.send_header("content-length", str(len(body)))
				self.end_headers()
				self.wfile.write(body)
			except Exception as error:
				LOG.warning("history export failed: %s", error)
				self.send_json(400, {"status": "error", "message": str(error)[:240]})
			return
		if self.path != "/health":
			self.send_json(404, {"status": "not_found"})
			return

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
			if self.path == "/private-gap-repair":
				result = asyncio.run(self.bridge.repair_private_positions(payload, reason="manual_private_gap_repair"))
				self.send_json(200, result)
				return
			self.send_json(404, {"status": "not_found"})
		except Exception as error:
			LOG.warning("bridge POST request failed path=%s: %s", self.path, error)
			self.send_json(400, {"status": "error", "message": str(error)[:240]})


class Bridge:
	def __init__(self) -> None:
		self.token = required_env("LARKX_BRIDGE_TOKEN")
		self.ingress_url = required_env("LARKX_INGRESS_URL")
		self.route_catalog_url = os.environ.get("LARKX_ROUTE_CATALOG_URL", "").strip()
		self.dynamic_route_discovery = os.environ.get("LARKX_DYNAMIC_ROUTE_DISCOVERY", "false").strip().lower() == "true"
		self.route_catalog_refresh_seconds = bounded_int_env("LARKX_ROUTE_CATALOG_REFRESH_SECONDS", DEFAULT_ROUTE_CATALOG_REFRESH_SECONDS, 5, 300)
		self.route_catalog: list[dict[str, str]] = []
		self.route_catalog_last_refresh_at = None
		self.route_catalog_error = None
		self.dynamic_routes: dict[str, dict[str, str]] = {}
		self.static_source_keys_by_chat = self._load_static_source_keys()
		self.anqiang_source_keys = parse_csv(os.environ.get("LARKX_ANQIANG_SOURCE_KEYS")) or {"anqiang"}
		self.anqiang_chat_ids = parse_csv(os.environ.get("LARKX_ANQIANG_CHAT_IDS"))
		configured_anqiang_keywords = parse_csv(os.environ.get("LARKX_ANQIANG_BLOCK_KEYWORDS"))
		self.anqiang_block_keywords = configured_anqiang_keywords or set(DEFAULT_ANQIANG_BLOCK_KEYWORDS)
		self.route_bindings_lock = threading.RLock()
		self.summary_chat_ids = csv_env("LARKX_SUMMARY_CHAT_IDS")
		self.summary_ingress_url = os.environ.get("LARKX_SUMMARY_INGRESS_URL", "").strip()
		if self.summary_chat_ids and not self.summary_ingress_url:
			raise RuntimeError("LARKX_SUMMARY_INGRESS_URL is required when LARKX_SUMMARY_CHAT_IDS is configured")
		self.listen_chats = csv_env("LARKX_LISTEN_CHAT_IDS") | self.summary_chat_ids
		self.send_chats = csv_env("LARKX_SEND_CHAT_IDS")
		if not self.listen_chats:
			raise RuntimeError("LARKX_LISTEN_CHAT_IDS must contain at least one chat ID")
		if not self.send_chats:
			raise RuntimeError("LARKX_SEND_CHAT_IDS must contain at least one chat ID")
		self.profile = os.environ.get("LARKX_PROFILE", "default").strip() or "default"
		larkx_home = Path(os.environ.get("LARKX_HOME", "~/.larkx")).expanduser()
		auth_path, default_spool_path, default_owner_path = profile_storage_paths(larkx_home, self.profile)
		auth_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
		self.route_bindings_path = Path(
			os.environ.get("LARKX_ROUTE_BINDINGS_FILE", str(auth_path.parent / "route-bindings.json"))
		).expanduser()
		self.route_bindings = self._load_route_bindings()
		spool_path = os.environ.get("LARKX_EVENT_SPOOL_DB", str(default_spool_path))
		self.event_spool = EventSpool(spool_path)
		history_path = os.environ.get("LARKX_HISTORY_DB", str(Path(spool_path).with_name("history.db")))
		self.history_archive = HistoryArchive(history_path)
		# Preserve events already retained by the spool when this capability is
		# enabled for the first time; future WebSocket events append below.
		self.history_archive.append_many(self.event_spool.all_payloads())
		self.owner_lock = OwnerLock(os.environ.get("LARKX_OWNER_LOCK_PATH", str(default_owner_path)))
		self.owner_lock.acquire()
		self.auth_path = auth_path
		forensic_path = os.environ.get("LARKX_PROTOCOL_FORENSICS_DIR", str(auth_path.parent / "protocol-forensics"))
		self.protocol_forensics = ProtocolForensics(forensic_path)
		self.auth = LarkAuth(path=auth_path)
		self.decode_error_count = 0
		self.last_decode_error_at = None
		self.last_decode_error = None
		self.last_forensic_capture = None
		self.decode_fallback_count = 0
		self.last_decode_fallback_at = None
		self.unknown_field_count = 0
		self.wire_mismatch_count = 0
		self.partial_frame_count = 0
		self.partial_entry_error_count = 0
		self.groups_skipped = 0
		self.last_protocol_telemetry = None
		self.recovery_count = 0
		self.startup_recovery_count = 0
		self.reconnect_recovery_count = 0
		self.partial_recovery_count = 0
		self.last_recovery_at = None
		self.last_recovery_reason = None
		self.last_recovery_result = None
		try:
			configured_gap_seconds = int(os.environ.get("LARKX_GAP_REPAIR_SECONDS", str(DEFAULT_GAP_REPAIR_SECONDS)))
		except ValueError:
			configured_gap_seconds = DEFAULT_GAP_REPAIR_SECONDS
		self.gap_repair_seconds = max(MIN_GAP_REPAIR_SECONDS, min(MAX_GAP_REPAIR_SECONDS, configured_gap_seconds))
		self.gap_repair_enabled = os.environ.get("LARKX_GAP_REPAIR_ENABLED", "false").strip().lower() == "true"
		self.private_gap_repair_enabled = os.environ.get("LARKX_PRIVATE_GAP_REPAIR_ENABLED", "false").strip().lower() == "true"
		self.private_gap_repair_on_start = os.environ.get("LARKX_PRIVATE_GAP_REPAIR_ON_START", "false").strip().lower() == "true"
		self.private_gap_repair_chat_ids = csv_env("LARKX_PRIVATE_GAP_REPAIR_CHAT_IDS")
		self.private_gap_repair_max_positions = bounded_int_env(
			"LARKX_PRIVATE_REPAIR_MAX_POSITIONS", DEFAULT_PRIVATE_REPAIR_MAX_POSITIONS, 1, MAX_PRIVATE_REPAIR_MAX_POSITIONS,
		)
		# A position gap can only be detected after a later WebSocket event. The
		# bounded tail check also catches a silent socket tail without using OAuth.
		self.private_tail_repair_enabled = (
			self.private_gap_repair_enabled
			and os.environ.get("LARKX_PRIVATE_TAIL_REPAIR_ENABLED", "true").strip().lower() == "true"
		)
		self.private_tail_repair_seconds = bounded_int_env(
			"LARKX_PRIVATE_TAIL_REPAIR_SECONDS",
			DEFAULT_PRIVATE_TAIL_REPAIR_SECONDS,
			MIN_PRIVATE_TAIL_REPAIR_SECONDS,
			MAX_PRIVATE_TAIL_REPAIR_SECONDS,
		)
		self.private_tail_repair_window = bounded_int_env(
			"LARKX_PRIVATE_TAIL_REPAIR_WINDOW",
			DEFAULT_PRIVATE_TAIL_REPAIR_WINDOW,
			1,
			self.private_gap_repair_max_positions,
		)
		self._private_repair_lock = threading.RLock()
		self._private_repair_in_flight: set[tuple[str, int, int]] = set()
		self._private_tail_repair_in_flight = False
		self._private_startup_repair_started = False
		self.private_repair_count = 0
		self.private_repair_message_count = 0
		self.private_repair_failed_count = 0
		self.last_private_repair_at = None
		self.last_private_repair_result = None
		self.private_tail_repair_count = 0
		self.last_private_tail_repair_at = None
		self.last_private_tail_repair_result = None
		self._recovery_in_flight = False
		self.client = RecoveringLarkClient(
			self.auth,
			on_decode_error=self.on_decode_error,
			on_decode_fallback=self.on_decode_fallback,
			on_connected=self.on_websocket_connected,
		)
		# WebSocket events use numeric chat ids. Keep official oc_ aliases in the
		# configuration for documentation, but do not count them as live sockets.
		self.websocket_chat_ids = {chat_id for chat_id in self.listen_chats if chat_id.isdigit()}
		self._apply_persisted_route_bindings()
		self.chat_validation = {
			chat_id: {"state": "pending", "name": None, "checked_at": None, "error": None}
			for chat_id in sorted(self.websocket_chat_ids)
		}
		self.observed_count = 0
		self.ignored_count = 0
		self.forwarded_count = 0
		self.retry_count = 0
		self.failed_count = 0
		self.self_message_count = 0
		self.last_observed_chat_id = ""
		self.last_observed_message_type = ""
		self.chat_stats = {
			chat_id: {
				"allowlisted": True, "observed_count": 0, "self_message_count": 0,
				"forwarded_count": 0, "failed_count": 0,
				"last_observed_at": None, "last_forwarded_at": None,
				"last_message_type": None,
			}
			for chat_id in self.websocket_chat_ids
		}
		self.websocket_attempt_count = 0
		self.websocket_state = "starting"
		self.last_websocket_attempt_at = None
		self.spool_replay_seconds = bounded_int_env("LARKX_SPOOL_REPLAY_SECONDS", DEFAULT_SPOOL_REPLAY_SECONDS, 5, 300)
		self.spool_replay_count = 0
		self.spool_replay_error_count = 0
		self.last_spool_replay_at = None
		self.last_spool_replay_error = None
		self._spool_active: set[str] = set()
		self.mapping_check_at = None
		self.mapping_check_error = None
		# Do not delay WebSocket startup on gateway probes. Mapping validation is
		# diagnostic and runs in the background so a slow private gateway cannot
		# make systemd treat the relay as unhealthy.
		threading.Thread(target=self.validate_chat_mappings, name="chat-mapping-check", daemon=True).start()
		if self.dynamic_route_discovery and self.route_catalog_url:
			threading.Thread(target=self.refresh_route_catalog, name="route-catalog-startup", daemon=True).start()
		LOG.info("listening to %d allowlisted chat(s): %s", len(self.listen_chats), ",".join(sorted(self.listen_chats)))

	def _load_static_source_keys(self) -> dict[str, str]:
		result: dict[str, str] = {}
		for pair in os.environ.get("LARKX_GROUP_RELAY_ROUTES", "").split(";"):
			if "=" not in pair:
				continue
			chat_id, source_key = (part.strip() for part in pair.split("=", 1))
			if chat_id.isdigit() and source_key:
				result[chat_id] = source_key
		return result

	def _source_route_for_chat(self, chat_id: str, dynamic_route: dict[str, str] | None = None) -> tuple[str, str]:
		chat_id = str(chat_id or "").strip()
		route = dynamic_route or self.dynamic_routes.get(chat_id) or {}
		source_key = str(route.get("source_key") or self.static_source_keys_by_chat.get(chat_id) or "").strip()
		chat_name = str(route.get("chat_name") or "").strip()
		if not source_key or not chat_name:
			for binding in self.route_bindings.values():
				if str(binding.get("chat_id") or "").strip() != chat_id:
					continue
				source_key = source_key or str(binding.get("source_key") or "").strip()
				chat_name = chat_name or str(binding.get("chat_name") or "").strip()
				break
		if not chat_name:
			validation = getattr(self, "chat_validation", {}).get(chat_id, {})
			chat_name = str(validation.get("name") or "").strip()
		return source_key, chat_name

	def _load_route_bindings(self) -> dict[str, dict[str, str]]:
		try:
			payload = json.loads(self.route_bindings_path.read_text(encoding="utf-8"))
		except FileNotFoundError:
			return {}
		except (OSError, json.JSONDecodeError) as error:
			LOG.warning("读取持久化 LarkAgentX 路由绑定失败：%s", error)
			return {}
		if not isinstance(payload, dict):
			return {}
		bindings = payload.get("bindings", payload)
		if not isinstance(bindings, dict):
			return {}
		result: dict[str, dict[str, str]] = {}
		for source_key, item in bindings.items():
			if not isinstance(item, dict):
				continue
			chat_id = str(item.get("chat_id", "")).strip()
			if not chat_id.isdigit():
				continue
			result[str(source_key).strip()] = {
				"source_key": str(item.get("source_key") or source_key).strip(),
				"chat_id": chat_id,
				"chat_name": str(item.get("chat_name", "")).strip(),
				"source_chat_id": str(item.get("source_chat_id", "")).strip(),
				"bound_at": str(item.get("bound_at", "")).strip(),
			}
		return {key: value for key, value in result.items() if key and value["source_key"]}

	def _route_stats_template(self) -> dict[str, Any]:
		return {
			"allowlisted": True, "observed_count": 0, "self_message_count": 0,
			"forwarded_count": 0, "failed_count": 0, "filtered_count": 0,
			"last_observed_at": None, "last_forwarded_at": None, "last_message_type": None,
		}

	def _register_runtime_route(self, chat_id: str, route: dict[str, str]) -> None:
		chat_id = str(chat_id).strip()
		if not chat_id.isdigit():
			return
		self.listen_chats.add(chat_id)
		self.websocket_chat_ids.add(chat_id)
		self.dynamic_routes[chat_id] = dict(route)
		if hasattr(self, "chat_validation"):
			self.chat_validation.setdefault(chat_id, {"state": "pending", "name": route.get("chat_name"), "checked_at": None, "error": None})
		if hasattr(self, "chat_stats"):
			self.chat_stats.setdefault(chat_id, self._route_stats_template())

	def _apply_persisted_route_bindings(self) -> None:
		for binding in self.route_bindings.values():
			chat_id = binding.get("chat_id", "")
			if not chat_id.isdigit():
				continue
			route = {
				"source_key": binding.get("source_key", ""),
				"chat_name": binding.get("chat_name", ""),
				"source_chat_id": binding.get("source_chat_id", ""),
			}
			self._register_runtime_route(chat_id, route)

	def _persist_route_binding(self, chat_id: str, route: dict[str, str]) -> None:
		chat_id = str(chat_id).strip()
		source_key = str(route.get("source_key", "")).strip()
		if not chat_id.isdigit() or not source_key:
			return
		binding = {
			"source_key": source_key,
			"chat_id": chat_id,
			"chat_name": str(route.get("chat_name", "")).strip(),
			"source_chat_id": str(route.get("source_chat_id", "")).strip(),
			"bound_at": datetime.now(timezone.utc).isoformat(),
		}
		try:
			with self.route_bindings_lock:
				self.route_bindings[source_key] = binding
				self.route_bindings_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
				temporary = self.route_bindings_path.with_name(f".{self.route_bindings_path.name}.{os.getpid()}.tmp")
				temporary.write_text(json.dumps({"version": 1, "bindings": self.route_bindings}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
				os.chmod(temporary, 0o600)
				os.replace(temporary, self.route_bindings_path)
		except OSError as error:
			LOG.warning("持久化 LarkAgentX 路由绑定失败 source_key=%s：%s", source_key, error)

	def _apply_catalog_bindings(self, routes: list[dict[str, str]]) -> None:
		for route in routes:
			source_key = route.get("source_key", "")
			binding = self.route_bindings.get(source_key, {})
			chat_id = route.get("source_chat_id", "")
			if not chat_id.isdigit():
				chat_id = str(binding.get("chat_id", "")).strip()
			if not chat_id.isdigit():
				continue
			runtime_route = dict(route)
			if not runtime_route.get("source_chat_id"):
				runtime_route["source_chat_id"] = str(binding.get("source_chat_id", "")).strip()
			self._register_runtime_route(chat_id, runtime_route)

	def refresh_route_catalog(self) -> None:
		if not self.dynamic_route_discovery or not self.route_catalog_url:
			return
		try:
			request = Request(self.route_catalog_url, headers={"accept": "application/json", "x-larkagentx-token": self.token})
			with urlopen(request, timeout=5) as response:
				payload = json.loads(response.read(MAX_BODY_BYTES).decode("utf-8"))
			routes = []
			for item in payload.get("routes", []) if isinstance(payload, dict) else []:
				if not isinstance(item, dict) or item.get("enabled") is False:
					continue
				source_key = str(item.get("source_key", "")).strip()
				chat_name = str(item.get("chat_name", "")).strip()
				chat_id = str(item.get("source_chat_id", "")).strip()
				# A route may be persisted before OAuth can resolve its oc_ ID
				# (for example while the tenant quota is exhausted).  Keep it in
				# the catalog with an empty source_chat_id; the private client can
				# identify the numeric WebSocket chat by its unique name when the
				# first event arrives.
				if source_key and chat_name:
					routes.append({"source_key": source_key, "chat_name": chat_name, "source_chat_id": chat_id})
			self.route_catalog = routes
			self._apply_catalog_bindings(routes)
			self.route_catalog_last_refresh_at = datetime.now(timezone.utc).isoformat()
			self.route_catalog_error = None
		except Exception as error:
			self.route_catalog_error = str(error)[:240]

	async def discover_dynamic_route(self, chat_id: str) -> dict[str, str] | None:
		if not self.dynamic_route_discovery or not self.route_catalog_url:
			return None
		known = self.dynamic_routes.get(chat_id)
		if known:
			return known
		await asyncio.to_thread(self.refresh_route_catalog)
		catalog = list(self.route_catalog)
		if not catalog:
			return None
		try:
			info = await asyncio.to_thread(self.client.get_chat_info, chat_id)
			name = str((info or {}).get("name") or "").strip()
		except Exception as error:
			self.route_catalog_error = f"{chat_id}: {error}"[:240]
			return None
		matches = [route for route in catalog if route["chat_name"] == name]
		if len(matches) != 1:
			if len(matches) > 1:
				self.route_catalog_error = f"群名重复，拒绝自动绑定：{name}"[:240]
			return None
		route = matches[0]
		self._register_runtime_route(chat_id, route)
		self._persist_route_binding(chat_id, route)
		self.chat_validation[chat_id] = {"state": "verified", "name": name, "checked_at": datetime.now(timezone.utc).isoformat(), "error": None}
		LOG.info("动态绑定 LarkAgentX 源群 chat_id=%s name=%s source_key=%s", chat_id, name, route["source_key"])
		return route

	def validate_chat_mappings(self) -> None:
		"""Resolve numeric WebSocket ids at startup without sending any message."""
		self.mapping_check_at = datetime.now(timezone.utc).isoformat()
		self.mapping_check_error = None
		for chat_id in sorted(self.websocket_chat_ids):
			try:
				info = self.client.get_chat_info(chat_id) or {}
				name = str(info.get("name") or "").strip()
				if not name:
					raise RuntimeError("群信息为空")
				self.chat_validation[chat_id] = {"state": "verified", "name": name, "checked_at": self.mapping_check_at, "error": None}
			except Exception as error:
				self.chat_validation[chat_id] = {"state": "error", "name": None, "checked_at": self.mapping_check_at, "error": str(error)[:240]}
				self.mapping_check_error = self.mapping_check_error or f"{chat_id}: {error}"

	def on_websocket_connected(self) -> None:
		"""Mark the socket healthy at handshake time, before the first event."""
		self.websocket_state = "connected"
		# Both process startup and later reconnects may hide events emitted while
		# the socket was down. The official repair path is ledger-idempotent, so a
		# bounded reconciliation is safer than assuming the first connection has
		# no preceding gap.
		if self.gap_repair_enabled and not self._recovery_in_flight:
			self._recovery_in_flight = True
			if self.websocket_attempt_count == 1:
				self.startup_recovery_count += 1
				self._persist_counter("startup_recovery_count")
				reason = "larkagentx_websocket_startup"
			else:
				self.reconnect_recovery_count += 1
				self._persist_counter("reconnect_recovery_count")
				reason = "larkagentx_websocket_reconnect"
			asyncio.create_task(self.recover_gap(reason))
		if self.private_gap_repair_enabled and self.private_gap_repair_on_start and not self._private_startup_repair_started:
			asyncio.create_task(self.repair_known_private_gaps())

	def health(self) -> dict[str, Any]:
		durable_chat_stats = self.event_spool.chat_stats()
		durable_summary = self.event_spool.chat_summary()
		durable_counters = self.event_spool.counters()
		durable_ignored = self.event_spool.ignored_stats()
		durable_ignored_summary = self.event_spool.ignored_summary()
		durable_positions = self.event_spool.position_stats()
		durable_position_summary = self.event_spool.position_summary()
		# The event spool is the durable source for message-level metrics. Keep
		# the live self-message counter from this process, but restore all relay
		# counters and timestamps after a restart or source-overlay hot deploy.
		visible_chat_stats = {}
		for chat_id in sorted(self.chat_stats):
			value = dict(durable_chat_stats.get(chat_id, self.chat_stats[chat_id]))
			value["self_message_count"] = self.chat_stats[chat_id].get("self_message_count", 0)
			visible_chat_stats[chat_id] = value
		return {
			"status": "ok",
			"component": "larkagentx-bridge",
			"runtime_source": os.environ.get("LARKX_BRIDGE_SOURCE_MODE", "base"),
			"release": os.environ.get("LARKX_BRIDGE_RELEASE") or None,
			"profile": self.profile,
			"auth_path": str(self.auth_path),
			"owner_lock": {"held": bool(self.owner_lock.held), "path": str(self.owner_lock.path)},
			"event_spool": {**self.event_spool.stats(), "path": str(self.event_spool.path), "replay_seconds": self.spool_replay_seconds, "replay_count": self.spool_replay_count, "replay_error_count": self.spool_replay_error_count, "last_replay_at": self.last_spool_replay_at, "last_error": self.last_spool_replay_error},
			"history_archive": {**self.history_archive.stats(), "path": str(self.history_archive.path)},
			"listen_chat_count": len(self.listen_chats),
			"listen_chat_ids": sorted(self.listen_chats),
			"websocket_chat_ids": sorted(self.websocket_chat_ids),
			"dynamic_route_discovery": self.dynamic_route_discovery,
			"route_catalog_count": len(self.route_catalog),
			"route_catalog_last_refresh_at": self.route_catalog_last_refresh_at,
			"route_catalog_error": self.route_catalog_error,
			"dynamic_routes": {chat_id: dict(route) for chat_id, route in sorted(self.dynamic_routes.items())},
			"persisted_route_bindings": {key: dict(value) for key, value in sorted(self.route_bindings.items())},
			"route_bindings_file": str(self.route_bindings_path),
			"chat_validation": {chat_id: dict(value) for chat_id, value in sorted(self.chat_validation.items())},
			"mapping_check_at": self.mapping_check_at,
			"mapping_check_error": self.mapping_check_error,
			"send_chat_count": len(self.send_chats),
			"metrics_persisted": True,
			"metrics_source": "larkagentx_event_spool",
			"observed_count": durable_summary["observed_count"],
			"ignored_count": max(self.ignored_count, durable_ignored_summary["ignored_count"]),
			"filtered_count": durable_summary.get("filtered_count", 0),
			"source_filters": {
				"anqiang_source_keys": sorted(self.anqiang_source_keys),
				"anqiang_chat_ids": sorted(self.anqiang_chat_ids),
				"anqiang_block_keywords": sorted(self.anqiang_block_keywords),
				"filtered_count": durable_summary.get("filtered_count", 0),
			},
			"ignored_by_chat": durable_ignored,
			"position_stats": durable_positions,
			"position_summary": durable_position_summary,
			"forwarded_count": durable_summary["forwarded_count"],
			"retry_count": self.retry_count,
			"failed_count": durable_summary["failed_count"],
			"historical_failed_count": durable_summary["historical_failed_count"],
			"self_message_count": self.self_message_count,
			"decode_error_count": durable_counters.get("decode_error_count", self.decode_error_count),
			"decode_fallback_count": durable_counters.get("decode_fallback_count", self.decode_fallback_count),
			"last_decode_fallback_at": self.last_decode_fallback_at,
			"unknown_field_count": durable_counters.get("unknown_field_count", self.unknown_field_count),
			"wire_mismatch_count": durable_counters.get("wire_mismatch_count", self.wire_mismatch_count),
			"partial_frame_count": durable_counters.get("partial_frame_count", self.partial_frame_count),
			"partial_entry_error_count": durable_counters.get("partial_entry_error_count", self.partial_entry_error_count),
			"groups_skipped": durable_counters.get("groups_skipped", self.groups_skipped),
			"last_protocol_telemetry": self.last_protocol_telemetry,
			"last_decode_error_at": self.last_decode_error_at,
			"last_decode_error": self.last_decode_error,
			"protocol_forensics": {"enabled": self.protocol_forensics.enabled, "path": str(self.protocol_forensics.path)},
			"last_forensic_capture": self.last_forensic_capture,
			"recovery_count": durable_counters.get("recovery_count", self.recovery_count),
			"startup_recovery_count": durable_counters.get("startup_recovery_count", self.startup_recovery_count),
			"reconnect_recovery_count": durable_counters.get("reconnect_recovery_count", self.reconnect_recovery_count),
			"partial_recovery_count": durable_counters.get("partial_recovery_count", self.partial_recovery_count),
			"last_recovery_at": self.last_recovery_at,
			"last_recovery_reason": self.last_recovery_reason,
			"last_recovery_result": self.last_recovery_result,
			"gap_repair_seconds": self.gap_repair_seconds,
			"gap_repair_enabled": self.gap_repair_enabled,
			"private_gap_repair_enabled": self.private_gap_repair_enabled,
			"private_gap_repair_on_start": self.private_gap_repair_on_start,
			"private_gap_repair_chat_ids": sorted(self.private_gap_repair_chat_ids),
			"private_gap_repair_max_positions": self.private_gap_repair_max_positions,
			"private_tail_repair_enabled": self.private_tail_repair_enabled,
			"private_tail_repair_seconds": self.private_tail_repair_seconds,
			"private_tail_repair_window": self.private_tail_repair_window,
			"private_tail_repair_count": self.private_tail_repair_count,
			"last_private_tail_repair_at": self.last_private_tail_repair_at,
			"last_private_tail_repair_result": self.last_private_tail_repair_result,
			"private_repair_count": durable_counters.get("private_repair_count", self.private_repair_count),
			"private_repair_message_count": durable_counters.get("private_repair_message_count", self.private_repair_message_count),
			"private_repair_failed_count": durable_counters.get("private_repair_failed_count", self.private_repair_failed_count),
			"last_private_repair_at": self.last_private_repair_at,
			"last_private_repair_result": self.last_private_repair_result,
			"summary_chat_ids": sorted(self.summary_chat_ids),
			"summary_ingress_configured": bool(self.summary_ingress_url),
			"last_observed_chat_id": self.last_observed_chat_id or None,
			"last_observed_message_type": self.last_observed_message_type or None,
			"websocket": {
				"state": self.websocket_state,
				"attempt_count": self.websocket_attempt_count,
				"last_attempt_at": self.last_websocket_attempt_at,
			},
			"chat_stats": visible_chat_stats,
			"user_id": self.auth.user_id or None,
		}

	def _persist_counter(self, name: str, amount: int = 1) -> None:
		try:
			self.event_spool.increment_counter(name, amount)
		except Exception as error:
			LOG.warning("无法持久化运行计数器 %s: %s", name, error)

	def on_decode_error(self, error: Exception, raw: bytes | None = None) -> None:
		self.decode_error_count += 1
		self._persist_counter("decode_error_count")
		self.last_decode_error_at = datetime.now(timezone.utc).isoformat()
		self.last_decode_error = {
			"type": type(error).__name__,
			"message": str(error)[:240],
			"telemetry": error.telemetry() if hasattr(error, "telemetry") else None,
		}
		if raw is not None:
			self.last_forensic_capture = self.protocol_forensics.capture(raw, error)
		# A malformed protobuf frame can be followed by several fragments. Keep
		# recovery bounded so this remains an exception path rather than OAuth
		# polling in disguise.
		last = datetime.fromisoformat(self.last_recovery_at) if self.last_recovery_at else None
		if not self.gap_repair_enabled or self._recovery_in_flight or (last and (datetime.now(timezone.utc) - last).total_seconds() < 30):
			return
		self._recovery_in_flight = True
		asyncio.create_task(self.recover_gap("larkagentx_websocket_decode_error"))

	def on_decode_fallback(self, error: Exception, frame_bytes: int, message_count: int, telemetry: dict[str, Any] | None = None) -> None:
		"""Record a schema-tolerant recovery without starting OAuth backfill."""
		self.decode_fallback_count += 1
		self._persist_counter("decode_fallback_count")
		self.last_decode_fallback_at = datetime.now(timezone.utc).isoformat()
		if telemetry:
			unknown_fields = telemetry.get("unknown_fields") or {}
			unknown_field_delta = sum(int(value) for value in unknown_fields.values())
			self.unknown_field_count += unknown_field_delta
			self._persist_counter("unknown_field_count", unknown_field_delta)
			wire_mismatches = telemetry.get("wire_mismatches") or {}
			wire_mismatch_delta = sum(int(value) for value in wire_mismatches.values())
			self.wire_mismatch_count += wire_mismatch_delta
			self._persist_counter("wire_mismatch_count", wire_mismatch_delta)
			entry_errors = telemetry.get("entry_errors") or []
			partial_delta = int(bool(telemetry.get("partial")))
			entry_error_delta = len(entry_errors)
			groups_skipped_delta = int(telemetry.get("groups_skipped") or 0)
			self.partial_frame_count += partial_delta
			self.partial_entry_error_count += entry_error_delta
			self.groups_skipped += groups_skipped_delta
			self._persist_counter("partial_frame_count", partial_delta)
			self._persist_counter("partial_entry_error_count", entry_error_delta)
			self._persist_counter("groups_skipped", groups_skipped_delta)
			self.last_protocol_telemetry = {
				"field_fingerprint": telemetry.get("field_fingerprint"),
				"unknown_fields": dict(unknown_fields),
				"wire_mismatches": dict(wire_mismatches),
				"entry_errors": list(entry_errors),
				"partial": bool(telemetry.get("partial")),
				"groups_skipped": int(telemetry.get("groups_skipped") or 0),
				"at": self.last_decode_fallback_at,
			}
			LOG.warning(
			"protobuf 主解码失败，已用容错 envelope 解码 frame_bytes=%d messages=%d fingerprint=%s unknown=%s groups=%s error=%s",
			frame_bytes, message_count,
			(telemetry or {}).get("field_fingerprint"),
			(telemetry or {}).get("unknown_fields"),
				(telemetry or {}).get("groups_skipped", 0), error,
			)
		if self.gap_repair_enabled and telemetry and telemetry.get("partial") and not self._recovery_in_flight:
			self._recovery_in_flight = True
			self.partial_recovery_count += 1
			self._persist_counter("partial_recovery_count")
			asyncio.create_task(self.recover_gap("larkagentx_partial_frame"))

	def _log_position_gap(self, chat_id: str, gap: dict[str, int] | None, *, reason: str = "") -> None:
		if not gap:
			return
		LOG.warning(
			"LarkAgentX position gap chat_id=%s previous=%s current=%s missing=%s-%s count=%s reason=%s",
			chat_id, gap["previous"], gap["current"], gap["start"], gap["end"], gap["missing"], reason or "observed",
		)

	def _private_repair_allowed(self, chat_id: str) -> bool:
		chat_id = str(chat_id or "").strip()
		return (
			chat_id.isdigit()
			and chat_id in self.websocket_chat_ids
			and (not self.private_gap_repair_chat_ids or chat_id in self.private_gap_repair_chat_ids)
		)

	def _private_repair_chunks(self, start: int, end: int) -> list[tuple[int, int]]:
		if end < start:
			return []
		chunk_size = max(1, self.private_gap_repair_max_positions)
		return [
			(chunk_start, min(end, chunk_start + chunk_size - 1))
			for chunk_start in range(start, end + 1, chunk_size)
		]

	def _schedule_private_gap(self, chat_id: str, gap: dict[str, int]) -> None:
		if not self.private_gap_repair_enabled or not self._private_repair_allowed(chat_id):
			return
		for start, end in self._private_repair_chunks(int(gap["start"]), int(gap["end"])):
			key = (str(chat_id), start, end)
			with self._private_repair_lock:
				if key in self._private_repair_in_flight:
					continue
				self._private_repair_in_flight.add(key)
			asyncio.create_task(self._run_scheduled_private_gap(key))

	async def _run_scheduled_private_gap(self, key: tuple[str, int, int]) -> None:
		chat_id, start, end = key
		try:
			await self.repair_private_positions(
				{"chat_id": chat_id, "positions": list(range(start, end + 1))},
				reason="live_websocket_position_gap",
			)
		finally:
			with self._private_repair_lock:
				self._private_repair_in_flight.discard(key)

	def _private_requests_from_payload(self, payload: dict[str, Any]) -> dict[str, list[int]]:
		requests: dict[str, set[int]] = {}

		def add(chat_id: Any, positions: Any = None, start: Any = None, end: Any = None) -> None:
			chat = str(chat_id or "").strip()
			if not self._private_repair_allowed(chat):
				raise ValueError(f"私有缺口补读群不在当前 WebSocket 白名单：{chat or 'unknown'}")
			values: set[int] = set()
			if isinstance(positions, (list, tuple, set)):
				for value in positions:
					try:
						position = int(value)
					except (TypeError, ValueError):
						continue
					if position > 0:
						values.add(position)
			if start is not None or end is not None:
				try:
					first = int(start)
					last = int(end)
				except (TypeError, ValueError) as error:
					raise ValueError("私有缺口补读范围格式错误") from error
				if first <= 0 or last < first:
					raise ValueError("私有缺口补读范围无效")
				values.update(range(first, last + 1))
			if not values:
				raise ValueError("私有缺口补读必须包含 positions 或 start/end")
			requests.setdefault(chat, set()).update(values)

		if isinstance(payload.get("requests"), list):
			for item in payload["requests"]:
				if not isinstance(item, dict):
					continue
				add(item.get("chat_id"), item.get("positions"), item.get("start"), item.get("end"))
		else:
			add(payload.get("chat_id"), payload.get("positions"), payload.get("start"), payload.get("end"))
		bounded_total = sum(len(values) for values in requests.values())
		if bounded_total > MAX_PRIVATE_REPAIR_MAX_POSITIONS * 4:
			raise ValueError("一次私有缺口补读最多允许 1024 个 position")
		return {chat_id: sorted(values) for chat_id, values in requests.items()}

	async def repair_private_positions(self, payload: dict[str, Any], *, reason: str) -> dict[str, Any]:
		"""Recover messages through LarkAgentX's private history gateway.

		This path intentionally never calls the official message.list API.  Each
		recovered message is sent through the same normalized ingress as a live
		WebSocket event, so image decryption, webhook fan-out and ledger idempotency
		remain centralized in the adapter.
		"""
		requests = self._private_requests_from_payload(payload)
		result: dict[str, Any] = {
			"status": "completed", "transport": "larkagentx_private_history", "reason": reason,
			"recovered": 0, "forwarded": 0, "duplicates": 0, "filtered": 0, "failed": 0,
			"requested": sum(len(values) for values in requests.values()), "sources": [],
		}
		for chat_id, positions in requests.items():
			source_result: dict[str, Any] = {"chat_id": chat_id, "requested": len(positions), "found": 0, "recovered": 0, "failed": 0}
			try:
				messages = await asyncio.to_thread(self.client.pull_history, chat_id, positions)
				source_result["found"] = len(messages)
			except Exception as error:
				source_result["failed"] += len(positions)
				result["failed"] += len(positions)
				source_result["error"] = str(error)[:240]
				result["sources"].append(source_result)
				continue
			requested = set(positions)
			for message in sorted(messages, key=lambda item: int(item.get("position") or 0)):
				try:
					position = int(message.get("position"))
				except (TypeError, ValueError):
					continue
				if position not in requested:
					continue
				try:
					delivery = await self.on_message(message, recovered=True)
					status = str((delivery or {}).get("status", ""))
					if status in {"delivered", "duplicate", "filtered"}:
						await asyncio.to_thread(self.event_spool.record_recovered_position, chat_id, position)
						result["recovered"] += 1
						source_result["recovered"] += 1
						if status == "delivered":
							result["forwarded"] += 1
						elif status == "duplicate":
							result["duplicates"] += 1
						else:
							result["filtered"] += 1
					else:
						result["failed"] += 1
						source_result["failed"] += 1
				except Exception as error:
					result["failed"] += 1
					source_result["failed"] += 1
					LOG.warning("LarkAgentX 私有缺口消息转发失败 chat_id=%s position=%s：%s", chat_id, position, error)
			result["sources"].append(source_result)
		if result["failed"]:
			result["status"] = "partial"
		self.private_repair_count += 1
		self.private_repair_message_count += int(result["recovered"])
		self.private_repair_failed_count += int(result["failed"])
		self._persist_counter("private_repair_count")
		self._persist_counter("private_repair_message_count", int(result["recovered"]))
		self._persist_counter("private_repair_failed_count", int(result["failed"]))
		self.last_private_repair_at = datetime.now(timezone.utc).isoformat()
		self.last_private_repair_result = {
			"status": result["status"], "reason": reason, "requested": result["requested"],
			"recovered": result["recovered"], "forwarded": result["forwarded"],
			"duplicates": result["duplicates"], "filtered": result["filtered"], "failed": result["failed"],
		}
		LOG.info("LarkAgentX 私有缺口补读完成：%s", self.last_private_repair_result)
		return result

	async def repair_known_private_gaps(self) -> None:
		if not self.private_gap_repair_enabled or not self.private_gap_repair_on_start or self._private_startup_repair_started:
			return
		self._private_startup_repair_started = True
		chat_ids = sorted(chat_id for chat_id in self.websocket_chat_ids if self._private_repair_allowed(chat_id))
		for chat_id in chat_ids:
			try:
				ranges = await asyncio.to_thread(self.event_spool.position_gap_ranges, chat_id)
				for gap in ranges:
					for start, end in self._private_repair_chunks(int(gap["start"]), int(gap["end"])):
						await self.repair_private_positions(
							{"chat_id": chat_id, "positions": list(range(start, end + 1))},
							reason="startup_private_position_gap",
						)
			except Exception as error:
				LOG.warning("LarkAgentX 启动私有缺口补读失败 chat_id=%s：%s", chat_id, error)

	async def repair_private_tail_once(self) -> dict[str, Any]:
		"""Boundedly reconcile each live WebSocket source's unseen position tail.

		Position-gap repair is event-driven and therefore cannot see a gap when
		there is no later WebSocket event. This check asks the private LarkAgentX
		history gateway for only the next small range after the durable cursor.
		"""
		if not self.private_tail_repair_enabled or self._private_tail_repair_in_flight:
			return {"status": "skipped", "reason": "disabled_or_in_flight"}
		self._private_tail_repair_in_flight = True
		result: dict[str, Any] = {
			"status": "completed",
			"transport": "larkagentx_private_history",
			"reason": "periodic_private_tail_repair",
			"requested": 0,
			"recovered": 0,
			"forwarded": 0,
			"duplicates": 0,
			"filtered": 0,
			"failed": 0,
			"sources": [],
		}
		try:
			for chat_id in sorted(self.websocket_chat_ids):
				if not self._private_repair_allowed(chat_id):
					continue
				try:
					stats = await asyncio.to_thread(self.event_spool.position_stats)
					cursor = stats.get(chat_id, {}) if isinstance(stats, dict) else {}
					last_position = int(cursor.get("last_position") or 0)
					last_recovered = int(cursor.get("last_recovered_position") or 0)
					start = max(last_position, last_recovered) + 1
					if start <= 1:
						continue
					end = start + self.private_tail_repair_window - 1
					chunk = await self.repair_private_positions(
						{"chat_id": chat_id, "start": start, "end": end},
						reason="periodic_private_tail_repair",
					)
					for key in ("requested", "recovered", "forwarded", "duplicates", "filtered", "failed"):
						result[key] += int(chunk.get(key) or 0)
					result["sources"].append({
						"chat_id": chat_id,
						"start": start,
						"end": end,
						"requested": chunk.get("requested", 0),
						"recovered": chunk.get("recovered", 0),
						"forwarded": chunk.get("forwarded", 0),
						"failed": chunk.get("failed", 0),
					})
				except Exception as error:
					result["failed"] += self.private_tail_repair_window
					result["sources"].append({"chat_id": chat_id, "error": str(error)[:240]})
					LOG.warning("LarkAgentX 私有尾部补读失败 chat_id=%s：%s", chat_id, error)
			if result["failed"]:
				result["status"] = "partial"
			self.private_tail_repair_count += 1
			self.last_private_tail_repair_at = datetime.now(timezone.utc).isoformat()
			self.last_private_tail_repair_result = {
				"status": result["status"],
				"requested": result["requested"],
				"recovered": result["recovered"],
				"forwarded": result["forwarded"],
				"duplicates": result["duplicates"],
				"filtered": result["filtered"],
				"failed": result["failed"],
			}
			return result
		finally:
			self._private_tail_repair_in_flight = False

	async def private_tail_repair_forever(self) -> None:
		while True:
			try:
				await self.repair_private_tail_once()
			except Exception as error:
				LOG.warning("LarkAgentX 私有尾部巡检异常：%s", error)
			await asyncio.sleep(self.private_tail_repair_seconds)

	async def recover_gap(self, reason: str) -> None:
		try:
			now_ms = int(time.time() * 1000)
			gap_url = self.ingress_url.rsplit('/', 1)[0] + '/gap-repair'
			payload = {
				"from_time": now_ms - self.gap_repair_seconds * 1000,
				"to_time": now_ms,
				"reason": reason,
				"source_chat_ids": sorted(self.websocket_chat_ids),
			}
			result = await asyncio.to_thread(post_json, gap_url, self.token, payload)
			self.recovery_count += 1
			self._persist_counter("recovery_count")
			self.last_recovery_at = datetime.now(timezone.utc).isoformat()
			self.last_recovery_reason = reason
			self.last_recovery_result = {"status": result.get("status"), "reason": reason, "sent": result.get("sent", 0), "deduplicated": result.get("deduplicated", 0), "failed": result.get("failed", 0)}
			LOG.info("WebSocket 解码异常后的缺口补读完成：%s", self.last_recovery_result)
		except Exception as recovery_error:
			self.last_recovery_reason = reason
			self.last_recovery_result = {"status": "error", "reason": reason, "message": str(recovery_error)[:240]}
			LOG.warning("WebSocket 解码异常后的缺口补读失败：%s", recovery_error)
		finally:
			self._recovery_in_flight = False

	def ingress_url_for(self, chat_id: str) -> str:
		return self.summary_ingress_url if chat_id in self.summary_chat_ids else self.ingress_url

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

	def resource(self, query: dict[str, str]) -> tuple[bytes, str]:
		"""Download one encrypted image payload using the personal-session cookies."""
		image_id = str(query.get("image_id", "")).strip()
		key_hex = str(query.get("key_hex", "")).strip()
		iv_hex = str(query.get("iv_hex", "")).strip()
		if not image_id or len(image_id) > 512:
			raise ValueError("image_id 不能为空或过长")
		try:
			key = bytes.fromhex(key_hex)
			iv = bytes.fromhex(iv_hex)
		except ValueError as error:
			raise ValueError("图片解密参数格式错误") from error
		if len(key) != 32 or len(iv) != 12:
			raise ValueError("图片解密参数长度错误")
		response = requests.get(
			image_download_url(image_id),
			cookies=self.auth.cookies,
			headers={"Referer": "https://xtool.feishu.cn/next/messenger/", "User-Agent": "Mozilla/5.0"},
			timeout=60,
		)
		if response.status_code < 200 or response.status_code >= 300:
			raise RuntimeError(f"图片 CDN HTTP {response.status_code}")
		encrypted = response.content
		if len(encrypted) > MAX_IMAGE_BYTES:
			raise ValueError("图片超过 64 MiB 转发上限")
		# The Node adapter performs AES-GCM with the key and IV carried alongside
		# this payload. Keeping the bridge as a cookie-authenticated downloader
		# avoids adding a second crypto implementation to the supervisor venv.
		return encrypted, response.headers.get("content-type", "application/octet-stream").split(";", 1)[0]

	async def on_message(self, message: dict[str, Any], *, recovered: bool = False) -> dict[str, Any]:
		# connect_websocket does not expose a separate connected callback.  Any
		# decoded event is proof that the WebSocket session is receiving data.
		self.websocket_state = "connected"
		chat_id = str(message.get("chat_id", ""))
		dynamic_route = self.dynamic_routes.get(chat_id)
		if chat_id not in self.websocket_chat_ids:
			dynamic_route = await self.discover_dynamic_route(chat_id)
		if chat_id not in self.websocket_chat_ids:
			self.ignored_count += 1
			try:
				gap = await asyncio.to_thread(
					self.event_spool.record_ignored,
					chat_id,
					position=message.get("position"),
					message_id=str(message.get("msg_id", "")),
					message_type=str(message.get("msg_type_name", message.get("msg_type", "UNKNOWN"))),
					reason="not_allowlisted_or_dynamic_binding_failed",
				)
				self._log_position_gap(chat_id, gap, reason="ignored")
				LOG.warning(
					"忽略未绑定 LarkAgentX 源群 chat_id=%s type=%s position=%s message_id=%s",
					chat_id, message.get("msg_type_name", message.get("msg_type", "UNKNOWN")), message.get("position"), message.get("msg_id", ""),
				)
			except Exception as error:
				LOG.warning("记录未绑定 LarkAgentX 源群指标失败 chat_id=%s：%s", chat_id, error)
			return {"status": "ignored", "chat_id": chat_id}
		stats = self.chat_stats[chat_id]
		try:
			gap = await asyncio.to_thread(self.event_spool.record_position, message)
			self._log_position_gap(chat_id, gap)
			if gap and not recovered:
				self._schedule_private_gap(chat_id, gap)
		except Exception as error:
			LOG.warning("记录 LarkAgentX position 失败 chat_id=%s：%s", chat_id, error)
		if str(message.get("from_id", "")) == str(self.auth.user_id):
			stats["self_message_count"] += 1
			self.self_message_count += 1
			return {"status": "filtered", "chat_id": chat_id, "message_id": str(message.get("msg_id", ""))}
		self.observed_count += 1
		stats["observed_count"] += 1
		stats["last_observed_at"] = datetime.now(timezone.utc).isoformat()
		self.last_observed_chat_id = chat_id
		self.last_observed_message_type = str(message.get("msg_type_name", message.get("msg_type", "UNKNOWN")))
		stats["last_message_type"] = self.last_observed_message_type
		message_id = str(message.get("msg_id", "")).strip()
		if not message_id:
			LOG.warning("dropping LarkAgentX message without msg_id")
			stats["failed_count"] += 1
			return {"status": "failed", "chat_id": chat_id, "message_id": ""}
		payload = json_safe(dict(message))
		if dynamic_route:
			payload["_larkagentx_route"] = dict(dynamic_route)
		source_key, chat_name = self._source_route_for_chat(chat_id, dynamic_route)
		blocked_keyword = matched_source_keyword(
			message,
			source_key=source_key,
			chat_name=chat_name,
			configured_source_keys=self.anqiang_source_keys,
			keywords=self.anqiang_block_keywords,
			chat_id=chat_id,
			configured_chat_ids=self.anqiang_chat_ids,
		)
		# Card v2 image-only messages store their media and AES-GCM metadata in
		# richtext element properties, just like POST images.  Extract them before
		# json_safe() turns protobuf bytes into replacement-text strings.
		if self.last_observed_message_type in {"IMAGE", "POST", "CARD", "INTERACTIVE"}:
			image = image_resource_info(message)
			if image.get("image_id"):
				payload["_larkagentx_image"] = image
			embedded = embedded_image_resources(message)
			if embedded:
				payload["_larkagentx_images"] = embedded
		payload["source_label"] = os.environ.get("LARKX_SOURCE_LABEL", "LarkAgentX 个人会话")
		event_id = f"larkagentx:{chat_id}:{message_id}"
		self.history_archive.append(event_id, payload)
		if blocked_keyword:
			payload["_larkagentx_filter"] = {
				"reason": "source_keyword",
				"keyword": blocked_keyword,
				"source_key": source_key,
			}
			try:
				inserted = await asyncio.to_thread(
					self.event_spool.record_filtered,
					event_id,
					chat_id,
					message_id=message_id,
					position=message.get("position"),
					message_type=self.last_observed_message_type,
					source_key=source_key,
					keyword=blocked_keyword,
					reason="source_keyword",
				)
				if inserted:
					stats["filtered_count"] = int(stats.get("filtered_count", 0)) + 1
				LOG.info("LarkAgentX 源消息命中过滤词，跳过 webhook：source=%s chat_id=%s type=%s message_id=%s keyword=%s", source_key or chat_name or "unknown", chat_id, self.last_observed_message_type, message_id, blocked_keyword)
				return {"status": "filtered", "chat_id": chat_id, "message_id": message_id, "filter_reason": "source_keyword"}
			except Exception as error:
				stats["failed_count"] += 1
				LOG.error("记录 LarkAgentX 源过滤事件失败 message_id=%s：%s", message_id, error)
				return {"status": "failed", "chat_id": chat_id, "message_id": message_id, "error": str(error)[:240]}
		try:
			spool_state = await asyncio.to_thread(self.event_spool.enqueue, event_id, payload)
			if spool_state == "delivered":
				return {"status": "duplicate", "chat_id": chat_id, "message_id": message_id}
			claim_state = await asyncio.to_thread(self.event_spool.claim, event_id)
			if claim_state in {"delivered", "in_flight"}:
				return {"status": "duplicate", "chat_id": chat_id, "message_id": message_id}
		except Exception as error:
			self.failed_count += 1
			stats["failed_count"] += 1
			LOG.error("inbound event spool failed message_id=%s: %s", message_id, error)
			return {"status": "failed", "chat_id": chat_id, "message_id": message_id, "error": str(error)[:240]}
		try:
			last_error: Exception | None = None
			for attempt in range(1, 4):
				try:
					result = await asyncio.to_thread(post_json, self.ingress_url_for(chat_id), self.token, payload)
					await asyncio.to_thread(self.event_spool.mark_delivered, event_id)
					self.forwarded_count += 1
					stats["forwarded_count"] += 1
					stats["last_forwarded_at"] = datetime.now(timezone.utc).isoformat()
					LOG.info("inbound accepted chat_id=%s type=%s message_id=%s status=%s", chat_id, self.last_observed_message_type, message_id, result.get("status"))
					return {"status": "delivered", "chat_id": chat_id, "message_id": message_id, "adapter_status": result.get("status")}
				except Exception as error:
					last_error = error
					if attempt < 3:
						self.retry_count += 1
						await asyncio.sleep(attempt)
			raise last_error or RuntimeError("inbound delivery failed")
		except Exception as error:
			await asyncio.to_thread(self.event_spool.mark_failed, event_id, str(error), retry_after=min(300, 10 * max(1, self.retry_count)))
			self.failed_count += 1
			stats["failed_count"] += 1
			LOG.error("inbound delivery failed message_id=%s: %s", message_id, error)
			return {"status": "failed", "chat_id": chat_id, "message_id": message_id, "error": str(error)[:240]}

	async def replay_spool_once(self) -> int:
		"""Replay queued/expired events; adapter-side ledger remains idempotent."""
		replayed = 0
		for event in await asyncio.to_thread(self.event_spool.due, 20):
			event_id = str(event["event_id"])
			if event_id in self._spool_active:
				continue
			self._spool_active.add(event_id)
			try:
				claim_state = await asyncio.to_thread(self.event_spool.claim, event_id)
				if claim_state in {"delivered", "in_flight"}:
					continue
				payload = event["payload"]
				chat_id = str(payload.get("chat_id", "")).strip()
				await asyncio.to_thread(post_json, self.ingress_url_for(chat_id), self.token, payload)
				await asyncio.to_thread(self.event_spool.mark_delivered, event_id)
				replayed += 1
				self.spool_replay_count += 1
			except Exception as error:
				self.spool_replay_error_count += 1
				self.last_spool_replay_error = str(error)[:240]
				await asyncio.to_thread(self.event_spool.mark_failed, event_id, str(error), retry_after=30)
			finally:
				self._spool_active.discard(event_id)
		self.last_spool_replay_at = datetime.now(timezone.utc).isoformat()
		return replayed

	async def replay_spool_forever(self) -> None:
		while True:
			try:
				await self.replay_spool_once()
			except Exception as error:
				self.spool_replay_error_count += 1
				self.last_spool_replay_error = str(error)[:240]
			await asyncio.sleep(self.spool_replay_seconds)

	async def listen_forever(self) -> None:
		spool_task = asyncio.create_task(self.replay_spool_forever())
		route_catalog_task = asyncio.create_task(self.refresh_route_catalog_forever()) if self.dynamic_route_discovery and self.route_catalog_url else None
		tail_repair_task = asyncio.create_task(self.private_tail_repair_forever()) if self.private_tail_repair_enabled else None
		try:
			while True:
				try:
					self.websocket_attempt_count += 1
					self.websocket_state = "connecting"
					self.last_websocket_attempt_at = datetime.now(timezone.utc).isoformat()
					await self.client.connect_websocket(self.on_message)
					self.websocket_state = "ended"
					LOG.warning("LarkAgentX websocket ended; reconnecting in 10 seconds")
				except AuthExpired:
					self.websocket_state = "auth_expired"
					LOG.error("LarkAgentX credentials expired; run lark auth qr or lark auth import")
					raise
				except Exception as error:
					self.websocket_state = "error"
					LOG.warning("LarkAgentX websocket failed: %s; reconnecting in 10 seconds", error)
				await asyncio.sleep(10)
		finally:
			spool_task.cancel()
			if route_catalog_task:
				route_catalog_task.cancel()
			if tail_repair_task:
				tail_repair_task.cancel()
			await asyncio.gather(
				spool_task,
				*( [route_catalog_task] if route_catalog_task else []),
				*( [tail_repair_task] if tail_repair_task else []),
				return_exceptions=True,
			)

	async def refresh_route_catalog_forever(self) -> None:
		while True:
			try:
				await asyncio.to_thread(self.refresh_route_catalog)
			except Exception as error:
				self.route_catalog_error = str(error)[:240]
			await asyncio.sleep(self.route_catalog_refresh_seconds)


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
		bridge.owner_lock.release()
		bridge.event_spool.close()


if __name__ == "__main__":
	main()
