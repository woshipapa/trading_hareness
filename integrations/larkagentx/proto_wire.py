"""Schema-tolerant protobuf envelope decoding for the LarkAgentX bridge.

This module deliberately knows only the wire envelope and the small set of
message fields consumed by the bridge.  It does not guess the meaning of
unknown fields.  Unknown, structurally valid fields are skipped and recorded
as bounded telemetry; malformed fields raise a layer-aware error so callers
can trigger official history repair.
"""

from __future__ import annotations

import hashlib
from typing import Any

from larkx.proto import decoders


EXPECTED_FIELDS = {
	"frame": {8},
	"packet": {1, 3, 5},
	"push": {1},
	"entry": {1, 2},
	"entity": {1, 2, 3, 4, 5, 8, 9, 10, 12, 13, 14, 20, 24, 46},
}

EXPECTED_WIRES = {
	"frame": {8: {2}},
	"packet": {1: {2}, 3: {0}, 5: {2}},
	"push": {1: {2}},
	"entry": {1: {2}, 2: {2}},
	"entity": {
		1: {2}, 2: {0}, 3: {2}, 4: {0}, 5: {2}, 8: {2}, 9: {2},
		10: {2}, 12: {2}, 13: {0}, 14: {0}, 20: {2}, 24: {2}, 46: {0},
	},
}


class TolerantProtoError(ValueError):
	"""A malformed wire payload with enough context for safe recovery."""

	def __init__(
		self,
		message: str,
		*,
		layer: str = "unknown",
		field_number: int | None = None,
		wire_type: int | None = None,
		offset: int | None = None,
	) -> None:
		super().__init__(message)
		self.layer = layer
		self.field_number = field_number
		self.wire_type = wire_type
		self.offset = offset

	def telemetry(self) -> dict[str, Any]:
		return {
			"layer": self.layer,
			"field_number": self.field_number,
			"wire_type": self.wire_type,
			"offset": self.offset,
			"message": str(self)[:240],
		}


def new_telemetry() -> dict[str, Any]:
	return {
		"unknown_fields": {},
		"wire_mismatches": {},
		"groups_skipped": 0,
		"entry_errors": [],
		"field_signatures": [],
	}


def _record_field(telemetry: dict[str, Any], layer: str, field_number: int, wire_type: int) -> None:
	telemetry["field_signatures"].append(f"{layer}:{field_number}:{wire_type}")
	if field_number not in EXPECTED_FIELDS.get(layer, set()):
		key = f"{layer}:{field_number}:{wire_type}"
		unknown = telemetry["unknown_fields"]
		unknown[key] = int(unknown.get(key, 0)) + 1
	elif wire_type not in EXPECTED_WIRES.get(layer, {}).get(field_number, set()):
		key = f"{layer}:{field_number}:{wire_type}"
		mismatches = telemetry["wire_mismatches"]
		mismatches[key] = int(mismatches.get(key, 0)) + 1


def _read_proto_varint(raw: bytes, offset: int, *, layer: str) -> tuple[int, int]:
	value = 0
	shift = 0
	start = offset
	while offset < len(raw) and shift <= 63:
		byte = raw[offset]
		offset += 1
		value |= (byte & 0x7F) << shift
		if not byte & 0x80:
			return value, offset
		shift += 7
	raise TolerantProtoError("truncated protobuf varint", layer=layer, offset=start)


def _read_proto_fields_until(
	raw: bytes,
	offset: int = 0,
	end_group: int | None = None,
	*,
	layer: str,
	telemetry: dict[str, Any],
	depth: int = 0,
) -> tuple[list[tuple[int, int, Any]], int]:
	if depth > 16:
		raise TolerantProtoError("protobuf nesting depth exceeded", layer=layer, offset=offset)
	fields: list[tuple[int, int, Any]] = []
	while offset < len(raw):
		tag, offset = _read_proto_varint(raw, offset, layer=layer)
		field_number, wire_type = tag >> 3, tag & 0x07
		field_offset = offset - 1
		if field_number <= 0:
			raise TolerantProtoError("invalid protobuf field number", layer=layer, offset=field_offset)
		if wire_type == 4:
			if end_group == field_number:
				return fields, offset
			raise TolerantProtoError("unexpected protobuf end-group tag", layer=layer, field_number=field_number, wire_type=wire_type, offset=field_offset)
		_record_field(telemetry, layer, field_number, wire_type)
		if wire_type == 3:
			telemetry["groups_skipped"] += 1
			_, offset = _read_proto_fields_until(raw, offset, field_number, layer=layer, telemetry=telemetry, depth=depth + 1)
			continue
		if wire_type == 0:
			value, offset = _read_proto_varint(raw, offset, layer=layer)
		elif wire_type == 1:
			end = offset + 8
			if end > len(raw):
				raise TolerantProtoError("truncated fixed64 protobuf field", layer=layer, field_number=field_number, wire_type=wire_type, offset=field_offset)
			value, offset = raw[offset:end], end
		elif wire_type == 2:
			length, offset = _read_proto_varint(raw, offset, layer=layer)
			end = offset + length
			if end > len(raw):
				raise TolerantProtoError("truncated protobuf bytes field", layer=layer, field_number=field_number, wire_type=wire_type, offset=field_offset)
			value, offset = raw[offset:end], end
		elif wire_type == 5:
			end = offset + 4
			if end > len(raw):
				raise TolerantProtoError("truncated fixed32 protobuf field", layer=layer, field_number=field_number, wire_type=wire_type, offset=field_offset)
			value, offset = raw[offset:end], end
		else:
			raise TolerantProtoError(
				f"unsupported protobuf wire type field={field_number} wire={wire_type} offset={field_offset} tag={tag}",
				layer=layer, field_number=field_number, wire_type=wire_type, offset=field_offset,
			)
		fields.append((field_number, wire_type, value))
	if end_group is not None:
		raise TolerantProtoError("unterminated protobuf group", layer=layer, field_number=end_group, wire_type=3, offset=offset)
	return fields, offset


def _read_proto_fields(raw: bytes, layer: str, telemetry: dict[str, Any]) -> list[tuple[int, int, Any]]:
	fields, offset = _read_proto_fields_until(raw, layer=layer, telemetry=telemetry)
	if offset != len(raw):
		raise TolerantProtoError("trailing protobuf bytes", layer=layer, offset=offset)
	return fields


def _proto_text(value: Any) -> str:
	return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else str(value)


def _first_proto_field(fields: list[tuple[int, int, Any]], number: int, wire_type: int | None = None) -> Any:
	for field_number, actual_wire_type, value in fields:
		if field_number == number and (wire_type is None or actual_wire_type == wire_type):
			return value
	return None


def _tolerant_entity_message(raw: bytes, telemetry: dict[str, Any]) -> dict[str, Any]:
	fields = _read_proto_fields(raw, "entity", telemetry)
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


def _tolerant_push_messages(raw: bytes, telemetry: dict[str, Any]) -> list[dict[str, Any]]:
	messages: list[dict[str, Any]] = []
	for field_number, wire_type, entry_raw in _read_proto_fields(raw, "push", telemetry):
		if field_number != 1 or wire_type != 2:
			continue
		try:
			entry = _read_proto_fields(entry_raw, "entry", telemetry)
			message_raw = _first_proto_field(entry, 2, 2)
			if not isinstance(message_raw, bytes):
				telemetry["entry_errors"].append({"layer": "entry", "message": "missing message payload"})
				continue
			message = _tolerant_entity_message(message_raw, telemetry)
			if message.get("msg_id"):
				messages.append(message)
		except TolerantProtoError as error:
			# A length-delimited entry has already been isolated by the push
			# parser.  Keep later entries usable and send this one to repair.
			if len(telemetry["entry_errors"]) < 32:
				telemetry["entry_errors"].append(error.telemetry())
	return messages


def _finalize_telemetry(telemetry: dict[str, Any]) -> dict[str, Any]:
	signatures = telemetry.pop("field_signatures", [])
	telemetry["field_fingerprint"] = hashlib.sha256("|".join(signatures).encode("ascii")).hexdigest()[:16] if signatures else None
	telemetry["partial"] = bool(telemetry.get("entry_errors"))
	return telemetry


def tolerant_websocket_decode_with_meta(raw: bytes) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
	"""Recover a gateway payload and return bounded schema telemetry."""
	telemetry = new_telemetry()
	frame = _read_proto_fields(raw, "frame", telemetry)
	packet_raw = _first_proto_field(frame, 8, 2)
	if not isinstance(packet_raw, bytes):
		raise TolerantProtoError("WebSocket frame has no packet payload", layer="frame")
	packet_fields = _read_proto_fields(packet_raw, "packet", telemetry)
	packet = {
		"sid": _proto_text(_first_proto_field(packet_fields, 1, 2) or ""),
		"cmd": int(_first_proto_field(packet_fields, 3, 0) or 0),
	}
	push_raw = _first_proto_field(packet_fields, 5, 2)
	messages = _tolerant_push_messages(push_raw, telemetry) if isinstance(push_raw, bytes) else []
	return packet, messages, _finalize_telemetry(telemetry)


def tolerant_websocket_decode(raw: bytes) -> tuple[dict[str, Any], list[dict[str, Any]]]:
	packet, messages, _ = tolerant_websocket_decode_with_meta(raw)
	return packet, messages
