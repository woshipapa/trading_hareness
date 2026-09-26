"""Source-scoped content filters for the private WebSocket ingress."""

from __future__ import annotations

import json
import unicodedata
from typing import Any, Iterable


DEFAULT_ANQIANG_BLOCK_KEYWORDS = ("般若星登山的川柏",)


def parse_csv(value: str | None) -> set[str]:
	return {part.strip() for part in str(value or "").replace("，", ",").split(",") if part.strip()}


def normalize_filter_text(value: Any) -> str:
	parts: list[str] = []
	collect_filter_text(value, parts)
	text = unicodedata.normalize("NFKC", "\n".join(parts)).casefold()
	return "".join(char for char in text if not char.isspace() and not unicodedata.category(char).startswith("P"))


def collect_filter_text(value: Any, parts: list[str], depth: int = 0) -> None:
	if depth > 16:
		return
	if isinstance(value, bytes):
		value = value.decode("utf-8", errors="replace")
	if isinstance(value, str):
		parts.append(value)
		candidate = value.strip()
		if candidate[:1] in {"{", "["}:
			try:
				parsed = json.loads(candidate)
			except (TypeError, json.JSONDecodeError):
				return
			if parsed != value:
				collect_filter_text(parsed, parts, depth + 1)
		return
	if isinstance(value, dict):
		for child in value.values():
			collect_filter_text(child, parts, depth + 1)
	elif isinstance(value, (list, tuple, set)):
		for child in value:
			collect_filter_text(child, parts, depth + 1)


def is_anqiang_source(
    source_key: str | None,
    chat_name: str | None,
    configured_keys: Iterable[str],
    *,
    chat_id: str | None = None,
    configured_chat_ids: Iterable[str] = (),
) -> bool:
	key = str(source_key or "").strip()
	name = str(chat_name or "").strip()
	keys = {str(item).strip() for item in configured_keys if str(item).strip()}
	chat_ids = {str(item).strip() for item in configured_chat_ids if str(item).strip()}
	return key in keys or "安强" in name or str(chat_id or "").strip() in chat_ids


def matched_source_keyword(
	message: dict[str, Any],
	*,
	source_key: str | None,
	chat_name: str | None,
	configured_source_keys: Iterable[str],
	keywords: Iterable[str],
	chat_id: str | None = None,
	configured_chat_ids: Iterable[str] = (),
) -> str | None:
	if not is_anqiang_source(source_key, chat_name, configured_source_keys, chat_id=chat_id, configured_chat_ids=configured_chat_ids):
		return None
	searchable = normalize_filter_text(message)
	if not searchable:
		return None
	for keyword in keywords:
		normalized = normalize_filter_text(keyword)
		if normalized and normalized in searchable:
			return str(keyword)
	return None
