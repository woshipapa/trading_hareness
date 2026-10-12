"""What the adapter's history bundle needs from the bridge and nothing else.

Two lookups, both read-only against Feishu:

* sender names: the WebSocket only carries ``from_id``; the gateway can turn
  one into the name the group shows.  Names rarely change, so they are cached
  on disk and a re-export costs no gateway calls.
* card images: a card's image keys and AES-GCM parameters exist only in the
  inbound payload the event spool kept (the history archive redacts them and
  the private history gateway returns cards with no content at all).

Kept free of larkx and of the bridge module so it is testable anywhere.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any, Callable, Iterable

MAX_NAME_LOOKUPS = 300
MAX_IMAGE_LOOKUPS = 5_000
# Mirror of adapter/card-content.mjs CARD_UNAVAILABLE_RES, as plain substrings:
# a card that carries this banner shows the banner's artwork, not content.
_BANNER_MARKERS = ("请升级至最新版本客户端", "upgrade to the latest app version to view the content")


class NameCache:
	"""user id -> display name, persisted as one small JSON file (0600)."""

	def __init__(self, path: str | os.PathLike[str]) -> None:
		self.path = Path(path).expanduser()
		self._lock = threading.RLock()
		self._names: dict[str, str] = {}
		try:
			loaded = json.loads(self.path.read_text(encoding="utf-8"))
			if isinstance(loaded, dict) and isinstance(loaded.get("names"), dict):
				self._names = {str(key): str(value) for key, value in loaded["names"].items() if str(value).strip()}
		except (FileNotFoundError, OSError, ValueError):
			self._names = {}

	def __len__(self) -> int:
		return len(self._names)

	def _flush_locked(self) -> None:
		self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
		temporary = self.path.with_name(f".{self.path.name}.{os.getpid()}.tmp")
		temporary.write_text(json.dumps({"version": 1, "names": self._names}, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
		os.chmod(temporary, 0o600)
		os.replace(temporary, self.path)

	def resolve(self, chat_id: str, user_ids: Iterable[Any], lookup: Callable[[str, str], Any]) -> dict[str, str]:
		"""Names for the numeric ids asked for; unknown ones go through ``lookup(user_id, chat_id)``.

		A lookup that fails or returns nothing is simply absent from the result
		and is not cached, so a later export tries again.
		"""
		wanted: list[str] = []
		for value in user_ids:
			user_id = str(value or "").strip()
			if user_id.isdigit() and user_id not in wanted:
				wanted.append(user_id)
		if len(wanted) > MAX_NAME_LOOKUPS:
			raise ValueError(f"一次最多解析 {MAX_NAME_LOOKUPS} 个发送者")
		found: dict[str, str] = {}
		learned = False
		for user_id in wanted:
			with self._lock:
				cached = self._names.get(user_id)
			if cached:
				found[user_id] = cached
				continue
			try:
				name = str(lookup(user_id, chat_id) or "").strip()
			except Exception:
				name = ""
			if name:
				found[user_id] = name
				with self._lock:
					self._names[user_id] = name
				learned = True
		if learned:
			try:
				with self._lock:
					self._flush_locked()
			except OSError:
				pass  # the names are still returned; only the cache write is lost
		return found


def _is_hex(value: Any, length: int) -> bool:
	text = str(value or "")
	return len(text) == length and all(char in "0123456789abcdefABCDEF" for char in text)


def _strings(value: Any, depth: int = 0) -> Iterable[str]:
	"""Every string under ``value``, looking inside strings that are themselves JSON
	(the card arrives as a JSON document in a string, sometimes \\u-escaped)."""
	if depth > 12:
		return
	if isinstance(value, (bytes, bytearray)):
		value = bytes(value).decode("utf-8", errors="replace")
	if isinstance(value, str):
		yield value
		# A whole JSON document, or an upstream summary such as "[卡片] {...}".
		stripped = value.strip()
		candidates = [stripped] if stripped[:1] in "{[" else []
		if "{" in stripped[1:64]:
			candidates.append(stripped[stripped.index("{", 1):])
		for candidate in candidates:
			try:
				yield from _strings(json.loads(candidate), depth + 1)
				break
			except ValueError:
				continue
	elif isinstance(value, dict):
		for child in value.values():
			yield from _strings(child, depth + 1)
	elif isinstance(value, (list, tuple)):
		for child in value:
			yield from _strings(child, depth + 1)


def stored_card_images(payload: Any) -> list[dict[str, str]]:
	"""Usable image resources of one spooled card payload, or [] if it has none worth exporting."""
	if not isinstance(payload, dict):
		return []
	if str(payload.get("msg_type_name") or payload.get("msg_type") or "").upper() not in {"CARD", "INTERACTIVE"}:
		return []
	if any(marker in text.lower() for text in _strings([payload.get("content"), payload.get("content_data")]) for marker in _BANNER_MARKERS):
		return []
	images: list[dict[str, str]] = []
	for item in payload.get("_larkagentx_images") or []:
		if not isinstance(item, dict):
			continue
		image_id = str(item.get("image_id") or "").strip()
		if not image_id or not _is_hex(item.get("key_hex"), 64) or not _is_hex(item.get("iv_hex"), 24):
			continue
		resource = {"image_id": image_id, "key_hex": str(item["key_hex"]), "iv_hex": str(item["iv_hex"])}
		if resource not in images:
			images.append(resource)
	return images


def card_images_by_message(requests: Any, load_payload: Callable[[str], Any]) -> dict[str, list[dict[str, str]]]:
	"""``requests`` is ``[{chat_id, message_id}, ...]``; ``load_payload(event_id)`` reads the spool."""
	if not isinstance(requests, list):
		raise ValueError("messages 必须是数组")
	if len(requests) > MAX_IMAGE_LOOKUPS:
		raise ValueError(f"一次最多查询 {MAX_IMAGE_LOOKUPS} 条消息")
	found: dict[str, list[dict[str, str]]] = {}
	for item in requests:
		if not isinstance(item, dict):
			continue
		chat_id = str(item.get("chat_id") or "").strip()
		message_id = str(item.get("message_id") or "").strip()
		if not chat_id.isdigit() or not message_id.isdigit() or message_id in found:
			continue
		images = stored_card_images(load_payload(f"larkagentx:{chat_id}:{message_id}"))
		if images:
			found[message_id] = images
	return found
