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
from proto_wire import tolerant_websocket_decode_with_meta


LOG = logging.getLogger("larkagentx-bridge")
MAX_BODY_BYTES = 64 * 1024
MAX_IMAGE_BYTES = 64 * 1024 * 1024
IMAGE_CDN_BASE_URL = "https://s1-imfile.feishucdn.com"
DEFAULT_GAP_REPAIR_SECONDS = 600
MIN_GAP_REPAIR_SECONDS = 120
MAX_GAP_REPAIR_SECONDS = 1800


class TolerantProtoError(ValueError):
	pass


def _read_proto_varint(raw: bytes, offset: int) -> tuple[int, int]:
	value = 0
	shift = 0
	while offset < len(raw) and shift <= 63:
		byte = raw[offset]
		offset += 1
		value |= (byte & 0x7F) << shift
		if not byte & 0x80:
			return value, offset
		shift += 7
	raise TolerantProtoError("truncated protobuf varint")


def _read_proto_fields_until(raw: bytes, offset: int = 0, end_group: int | None = None) -> tuple[list[tuple[int, int, Any]], int]:
	"""Read protobuf fields and safely skip deprecated group wire types.

	Some LarkAgentX gateway frames contain an unknown protobuf group around
	metadata.  The generated decoder rejects the whole frame and the previous
	fallback did the same.  Groups are not part of the fields we consume, so
	we parse through their matching end tag and continue with the envelope.
	"""
	fields: list[tuple[int, int, Any]] = []
	while offset < len(raw):
		tag, offset = _read_proto_varint(raw, offset)
		field_number, wire_type = tag >> 3, tag & 0x07
		if field_number <= 0:
			raise TolerantProtoError("invalid protobuf field number")
		if wire_type == 4:
			if end_group == field_number:
				return fields, offset
			raise TolerantProtoError("unexpected protobuf end-group tag")
		if wire_type == 3:
			_, offset = _read_proto_fields_until(raw, offset, field_number)
			continue
		if wire_type == 0:
			value, offset = _read_proto_varint(raw, offset)
		elif wire_type == 1:
			end = offset + 8
			if end > len(raw): raise TolerantProtoError("truncated fixed64 protobuf field")
			value, offset = raw[offset:end], end
		elif wire_type == 2:
			length, offset = _read_proto_varint(raw, offset)
			end = offset + length
			if end > len(raw): raise TolerantProtoError("truncated protobuf bytes field")
			value, offset = raw[offset:end], end
		elif wire_type == 5:
			end = offset + 4
			if end > len(raw): raise TolerantProtoError("truncated fixed32 protobuf field")
			value, offset = raw[offset:end], end
		else:
			raise TolerantProtoError(f"unsupported protobuf wire type field={field_number} wire={wire_type} offset={offset - 1} tag={tag}")
		fields.append((field_number, wire_type, value))
	if end_group is not None:
		raise TolerantProtoError("unterminated protobuf group")
	return fields, offset


def _read_proto_fields(raw: bytes) -> list[tuple[int, int, Any]]:
	"""Read only the protobuf wire envelope, ignoring generated schema types.

	Feishu occasionally reuses a known field with a different wire type in a
	new gateway envelope.  The generated descriptor then rejects the entire
	PushMessagesRequest even though the surrounding length-delimited messages
	are still recoverable.  This reader is deliberately limited to the four
	ordinary protobuf wire types and never guesses application data.
	"""
	fields, offset = _read_proto_fields_until(raw)
	if offset != len(raw):
		raise TolerantProtoError("trailing protobuf bytes")
	return fields


def _proto_text(value: Any) -> str:
	return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else str(value)


def _first_proto_field(fields: list[tuple[int, int, Any]], number: int, wire_type: int | None = None) -> Any:
	for field_number, actual_wire_type, value in fields:
		if field_number == number and (wire_type is None or actual_wire_type == wire_type):
			return value
	return None


def _tolerant_entity_message(raw: bytes) -> dict[str, Any]:
	fields = _read_proto_fields(raw)
	strings = {1: "id", 3: "from_id", 8: "root_id", 9: "parent_id", 10: "chat_id", 12: "cid", 20: "thread_id", 24: "channel_id"}
	integers = {2: "msg_type", 4: "create_time", 13: "position", 14: "update_time", 46: "chat_type"}
	message: dict[str, Any] = {}
	for field_number, wire_type, value in fields:
		if field_number in strings and wire_type == 2:
			message[strings[field_number]] = _proto_text(value)
		elif field_number in integers and wire_type == 0:
			message[integers[field_number]] = int(value)
		elif field_number == 5 and wire_type == 2:
			message["content_bytes"] = value
	message.setdefault("msg_type", 0)
	message.setdefault("chat_type", 0)
	content_bytes = message.get("content_bytes", b"")
	try:
		summary, content_data = decoders.decode_message_content(message["msg_type"], content_bytes)
	except Exception as error:
		summary, content_data = f"[{decoders.MSG_TYPE_NAMES.get(message['msg_type'], message['msg_type'])}消息解析失败: {error}]", None
	chat_type = int(message.get("chat_type", 0))
	root_id = message.get("root_id", "")
	thread_id = message.get("thread_id", "")
	if chat_type == 1:
		scope, anchor = "chat", ""
	elif root_id and root_id != "0":
		scope, anchor = "topic", thread_id or root_id
	elif chat_type == 3:
		scope, anchor = "chat", message.get("chat_id", "")
	else:
		scope, anchor = "chat", ""
	return {
		"msg_id": message.get("id", ""),
		"msg_type": message["msg_type"],
		"msg_type_name": decoders.MSG_TYPE_NAMES.get(message["msg_type"], str(message["msg_type"])),
		"from_id": message.get("from_id", ""),
		"chat_id": message.get("chat_id") or message.get("channel_id", ""),
		"chat_type": chat_type,
		"chat_type_name": decoders.CHAT_TYPE_NAMES.get(chat_type, str(chat_type)),
		"scope": scope,
		"anchor": anchor,
		"at_me": False,
		"root_id": root_id,
		"parent_id": message.get("parent_id", ""),
		"cid": message.get("cid", ""),
		"position": message.get("position"),
		"create_time": message.get("create_time"),
		"update_time": message.get("update_time"),
		"content": summary,
		"content_data": content_data,
	}


def _tolerant_push_messages(raw: bytes) -> list[dict[str, Any]]:
	messages: list[dict[str, Any]] = []
	for field_number, wire_type, entry_raw in _read_proto_fields(raw):
		if field_number != 1 or wire_type != 2:
			continue
		entry = _read_proto_fields(entry_raw)
		message_raw = _first_proto_field(entry, 2, 2)
		if not isinstance(message_raw, bytes):
			continue
		message = _tolerant_entity_message(message_raw)
		if message.get("msg_id"):
			messages.append(message)
	return messages


def tolerant_websocket_decode(raw: bytes) -> tuple[dict[str, Any], list[dict[str, Any]]]:
	"""Recover a valid gateway payload when generated protobuf parsing rejects it."""
	frame = _read_proto_fields(raw)
	packet_raw = _first_proto_field(frame, 8, 2)
	if not isinstance(packet_raw, bytes):
		raise TolerantProtoError("WebSocket frame has no packet payload")
	packet_fields = _read_proto_fields(packet_raw)
	packet = {
		"sid": _proto_text(_first_proto_field(packet_fields, 1, 2) or ""),
		"cmd": int(_first_proto_field(packet_fields, 3, 0) or 0),
	}
	push_raw = _first_proto_field(packet_fields, 5, 2)
	if isinstance(push_raw, bytes):
		return packet, _tolerant_push_messages(push_raw)
	return packet, []


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
							_, packet = decoders.parse_ws_frame(raw)
							messages = decoders.decode_push_messages(raw) if packet.get('cmd') == 6 else []
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
								self.on_decode_error(error)
							except Exception:
								pass
						logging.getLogger("larkagentx-bridge").warning("跳过无法恢复的 WebSocket 帧 len=%d sha256=%s: %s", len(raw), hashlib.sha256(raw).hexdigest()[:16], error)
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
		self.decode_error_count = 0
		self.last_decode_error_at = None
		self.decode_fallback_count = 0
		self.last_decode_fallback_at = None
		self.unknown_field_count = 0
		self.wire_mismatch_count = 0
		self.partial_frame_count = 0
		self.partial_entry_error_count = 0
		self.groups_skipped = 0
		self.last_protocol_telemetry = None
		self.recovery_count = 0
		self.last_recovery_at = None
		self.last_recovery_result = None
		try:
			configured_gap_seconds = int(os.environ.get("LARKX_GAP_REPAIR_SECONDS", str(DEFAULT_GAP_REPAIR_SECONDS)))
		except ValueError:
			configured_gap_seconds = DEFAULT_GAP_REPAIR_SECONDS
		self.gap_repair_seconds = max(MIN_GAP_REPAIR_SECONDS, min(MAX_GAP_REPAIR_SECONDS, configured_gap_seconds))
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
		self.mapping_check_at = None
		self.mapping_check_error = None
		# Do not delay WebSocket startup on gateway probes. Mapping validation is
		# diagnostic and runs in the background so a slow private gateway cannot
		# make systemd treat the relay as unhealthy.
		threading.Thread(target=self.validate_chat_mappings, name="chat-mapping-check", daemon=True).start()
		LOG.info("listening to %d allowlisted chat(s): %s", len(self.listen_chats), ",".join(sorted(self.listen_chats)))

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

	def health(self) -> dict[str, Any]:
		return {
			"status": "ok",
			"component": "larkagentx-bridge",
			"runtime_source": os.environ.get("LARKX_BRIDGE_SOURCE_MODE", "base"),
			"release": os.environ.get("LARKX_BRIDGE_RELEASE") or None,
			"listen_chat_count": len(self.listen_chats),
			"listen_chat_ids": sorted(self.listen_chats),
			"websocket_chat_ids": sorted(self.websocket_chat_ids),
			"chat_validation": {chat_id: dict(value) for chat_id, value in sorted(self.chat_validation.items())},
			"mapping_check_at": self.mapping_check_at,
			"mapping_check_error": self.mapping_check_error,
			"send_chat_count": len(self.send_chats),
			"observed_count": self.observed_count,
			"ignored_count": self.ignored_count,
			"forwarded_count": self.forwarded_count,
			"retry_count": self.retry_count,
			"failed_count": self.failed_count,
			"self_message_count": self.self_message_count,
			"decode_error_count": self.decode_error_count,
			"decode_fallback_count": self.decode_fallback_count,
			"last_decode_fallback_at": self.last_decode_fallback_at,
			"unknown_field_count": self.unknown_field_count,
			"wire_mismatch_count": self.wire_mismatch_count,
			"partial_frame_count": self.partial_frame_count,
			"partial_entry_error_count": self.partial_entry_error_count,
			"groups_skipped": self.groups_skipped,
			"last_protocol_telemetry": self.last_protocol_telemetry,
			"last_decode_error_at": self.last_decode_error_at,
			"recovery_count": self.recovery_count,
			"last_recovery_at": self.last_recovery_at,
			"last_recovery_result": self.last_recovery_result,
			"gap_repair_seconds": self.gap_repair_seconds,
			"last_observed_chat_id": self.last_observed_chat_id or None,
			"last_observed_message_type": self.last_observed_message_type or None,
			"websocket": {
				"state": self.websocket_state,
				"attempt_count": self.websocket_attempt_count,
				"last_attempt_at": self.last_websocket_attempt_at,
			},
			"chat_stats": {chat_id: dict(self.chat_stats[chat_id]) for chat_id in sorted(self.chat_stats)},
			"user_id": self.auth.user_id or None,
		}

	def on_decode_error(self, error: Exception) -> None:
		self.decode_error_count += 1
		self.last_decode_error_at = datetime.now(timezone.utc).isoformat()
		# A malformed protobuf frame can be followed by several fragments. Keep
		# recovery bounded so this remains an exception path rather than OAuth
		# polling in disguise.
		last = datetime.fromisoformat(self.last_recovery_at) if self.last_recovery_at else None
		if self._recovery_in_flight or (last and (datetime.now(timezone.utc) - last).total_seconds() < 30):
			return
		self._recovery_in_flight = True
		asyncio.create_task(self.recover_gap())

	def on_decode_fallback(self, error: Exception, frame_bytes: int, message_count: int, telemetry: dict[str, Any] | None = None) -> None:
		"""Record a schema-tolerant recovery without starting OAuth backfill."""
		self.decode_fallback_count += 1
		self.last_decode_fallback_at = datetime.now(timezone.utc).isoformat()
		if telemetry:
			unknown_fields = telemetry.get("unknown_fields") or {}
			self.unknown_field_count += sum(int(value) for value in unknown_fields.values())
			wire_mismatches = telemetry.get("wire_mismatches") or {}
			self.wire_mismatch_count += sum(int(value) for value in wire_mismatches.values())
			entry_errors = telemetry.get("entry_errors") or []
			self.partial_frame_count += int(bool(telemetry.get("partial")))
			self.partial_entry_error_count += len(entry_errors)
			self.groups_skipped += int(telemetry.get("groups_skipped") or 0)
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

	async def recover_gap(self) -> None:
		try:
			now_ms = int(time.time() * 1000)
			gap_url = self.ingress_url.rsplit('/', 1)[0] + '/gap-repair'
			payload = {
				"from_time": now_ms - self.gap_repair_seconds * 1000,
				"to_time": now_ms,
				"reason": "larkagentx_websocket_decode_error",
				"source_chat_ids": sorted(self.websocket_chat_ids),
			}
			result = await asyncio.to_thread(post_json, gap_url, self.token, payload)
			self.recovery_count += 1
			self.last_recovery_at = datetime.now(timezone.utc).isoformat()
			self.last_recovery_result = {"status": result.get("status"), "sent": result.get("sent", 0), "deduplicated": result.get("deduplicated", 0), "failed": result.get("failed", 0)}
			LOG.info("WebSocket 解码异常后的缺口补读完成：%s", self.last_recovery_result)
		except Exception as recovery_error:
			self.last_recovery_result = {"status": "error", "message": str(recovery_error)[:240]}
			LOG.warning("WebSocket 解码异常后的缺口补读失败：%s", recovery_error)
		finally:
			self._recovery_in_flight = False

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

	async def on_message(self, message: dict[str, Any]) -> None:
		# connect_websocket does not expose a separate connected callback.  Any
		# decoded event is proof that the WebSocket session is receiving data.
		self.websocket_state = "connected"
		chat_id = str(message.get("chat_id", ""))
		if chat_id not in self.listen_chats:
			self.ignored_count += 1
			return
		if chat_id not in self.websocket_chat_ids:
			self.ignored_count += 1
			return
		stats = self.chat_stats[chat_id]
		if str(message.get("from_id", "")) == str(self.auth.user_id):
			stats["self_message_count"] += 1
			self.self_message_count += 1
			return
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
			return
		payload = json_safe(dict(message))
		if self.last_observed_message_type in {"IMAGE", "POST"}:
			image = image_resource_info(message)
			if image.get("image_id"):
				payload["_larkagentx_image"] = image
			embedded = embedded_image_resources(message)
			if embedded:
				payload["_larkagentx_images"] = embedded
		payload["source_label"] = os.environ.get("LARKX_SOURCE_LABEL", "LarkAgentX 个人会话")
		try:
			last_error: Exception | None = None
			for attempt in range(1, 4):
				try:
					result = await asyncio.to_thread(post_json, self.ingress_url, self.token, payload)
					self.forwarded_count += 1
					stats["forwarded_count"] += 1
					stats["last_forwarded_at"] = datetime.now(timezone.utc).isoformat()
					LOG.info("inbound accepted chat_id=%s type=%s message_id=%s status=%s", chat_id, self.last_observed_message_type, message_id, result.get("status"))
					return
				except Exception as error:
					last_error = error
					if attempt < 3:
						self.retry_count += 1
						await asyncio.sleep(attempt)
			raise last_error or RuntimeError("inbound delivery failed")
		except Exception as error:
			self.failed_count += 1
			stats["failed_count"] += 1
			LOG.error("inbound delivery failed message_id=%s: %s", message_id, error)

	async def listen_forever(self) -> None:
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
