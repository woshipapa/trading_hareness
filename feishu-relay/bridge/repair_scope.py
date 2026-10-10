"""Which WebSocket chats the private gap/tail repair may backfill.

Kept free of bridge dependencies so the rule can be tested without larkx.
A chat bound at runtime by name (dynamic route) is repaired like any other
subscribed chat; otherwise a dashboard-added group would silently miss gap
repair until someone edited the env allowlist.
"""

from __future__ import annotations

from collections.abc import Iterable


def repair_allowed(
	chat_id: str,
	*,
	websocket_chat_ids: Iterable[str],
	configured_chat_ids: Iterable[str],
	dynamic_chat_ids: Iterable[str],
) -> bool:
	chat_id = str(chat_id or "").strip()
	if not chat_id.isdigit() or chat_id not in set(websocket_chat_ids):
		return False
	configured = set(configured_chat_ids)
	if not configured:
		return True
	return chat_id in configured or chat_id in set(dynamic_chat_ids)
