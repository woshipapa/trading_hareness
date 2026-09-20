"""Extract encrypted image metadata carried by rich-text image elements.

Feishu's rich-text image element stores an opaque protobuf in
``RichTextElement.property``.  The current LarkAgentX schema deliberately
keeps that field as bytes, so this small parser only walks protobuf wire
types and extracts the stable image key / AES-GCM pair without depending on
an undocumented generated message class.
"""

from __future__ import annotations

import re
from typing import Any, Iterator


_IMAGE_KEY_RE = re.compile(r"^img_v\d+_.+", re.IGNORECASE)


def _read_varint(data: bytes, offset: int) -> tuple[int, int]:
	value = 0
	shift = 0
	while offset < len(data) and shift < 70:
		byte = data[offset]
		offset += 1
		value |= (byte & 0x7F) << shift
		if not byte & 0x80:
			return value, offset
		shift += 7
	raise ValueError("invalid protobuf varint")


def _fields(data: bytes) -> Iterator[tuple[int, int, Any]]:
	offset = 0
	while offset < len(data):
		key, offset = _read_varint(data, offset)
		field_number = key >> 3
		wire_type = key & 0x07
		if field_number <= 0:
			raise ValueError("invalid protobuf field number")
		if wire_type == 0:
			value, offset = _read_varint(data, offset)
		elif wire_type == 1:
			if offset + 8 > len(data):
				raise ValueError("truncated fixed64 protobuf field")
			value = data[offset:offset + 8]
			offset += 8
		elif wire_type == 2:
			length, offset = _read_varint(data, offset)
			if length > len(data) - offset:
				raise ValueError("truncated protobuf bytes field")
			value = data[offset:offset + length]
			offset += length
		elif wire_type == 5:
			if offset + 4 > len(data):
				raise ValueError("truncated fixed32 protobuf field")
			value = data[offset:offset + 4]
			offset += 4
		else:
			# Groups are not used by this payload.  Treating them as an invalid
			# candidate makes the caller fail closed instead of guessing.
			raise ValueError("unsupported protobuf wire type")
		yield field_number, wire_type, value


def _find_crypto_pair(data: bytes) -> tuple[bytes, bytes] | None:
	try:
		items = list(_fields(data))
	except ValueError:
		return None

	key = next((value for number, wire, value in items if number == 1 and wire == 2 and isinstance(value, bytes) and len(value) == 32), None)
	iv = next((value for number, wire, value in items if number == 2 and wire == 2 and isinstance(value, bytes) and len(value) == 12), None)
	if key is not None and iv is not None:
		return key, iv

	for _, wire, value in items:
		if wire != 2 or not isinstance(value, bytes) or not value:
			continue
		found = _find_crypto_pair(value)
		if found is not None:
			return found
	return None


def _find_image_key(data: bytes) -> str:
	try:
		items = list(_fields(data))
	except ValueError:
		return ""
	for _, wire, value in items:
		if wire != 2 or not isinstance(value, bytes):
			continue
		try:
			text = value.decode("utf-8").strip()
		except UnicodeDecodeError:
			text = ""
		if text and _IMAGE_KEY_RE.fullmatch(text):
			return text
	for _, wire, value in items:
		if wire != 2 or not isinstance(value, bytes) or not value:
			continue
		found = _find_image_key(value)
		if found:
			return found
	return ""


def extract_rich_text_image_resource(property_bytes: bytes, source_id: str = "") -> dict[str, str]:
	"""Return image key and AES-GCM fields from one image element property.

	The result may contain only ``image_id`` when a future payload omits crypto;
	the relay must then keep the message on its official-resource fallback path.
	"""
	if not isinstance(property_bytes, (bytes, bytearray)) or not property_bytes:
		return {}
	data = bytes(property_bytes)
	image_id = _find_image_key(data)
	crypto = _find_crypto_pair(data)
	if not image_id and crypto is None:
		return {}
	result = {"image_id": image_id}
	if crypto is not None:
		result["key_hex"] = crypto[0].hex()
		result["iv_hex"] = crypto[1].hex()
	if source_id:
		result["source_id"] = str(source_id)
	return result
