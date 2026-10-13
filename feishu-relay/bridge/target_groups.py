"""Creating and configuring target groups."""

from __future__ import annotations

import uuid
from typing import Any

from group_admin import checked, require_client, require_owner


def _chat_id(payload: dict[str, Any]) -> str:
	chat_id = str(payload.get("chat_id", "")).strip()
	if not chat_id.isdigit():
		raise ValueError("chat_id 必须是数字群 ID")
	return chat_id


def create(bridge, payload: dict[str, Any]) -> dict[str, Any]:
	"""Create a cross-tenant group owned by the logged-in account."""
	name = str(payload.get("name", "")).strip()
	if not name or len(name) > 60:
		raise ValueError("群名必须是 1–60 个字")
	description = str(payload.get("description", "")).strip()
	client = require_client(bridge)
	result = checked(client.api("chats.PutChatRequest", {
		"type": "GROUP",
		"groupName": name,
		"groupDesc": description,
		"isCrossTenant": True,
		"cid": uuid.uuid4().hex,
	}))
	chat = result["chat"]
	bridge._joined_groups_cache = None
	return {"status": "ok", "chat_id": str(chat["id"]), "name": str(chat.get("name") or name)}


def _owned_client(bridge, payload: dict[str, Any]):
	chat_id = _chat_id(payload)
	client = require_client(bridge)
	info = client.chat_infos([chat_id]).get(chat_id) or {}
	require_owner(info, str(bridge.auth.user_id))
	return client, chat_id


def post_permission(bridge, payload: dict[str, Any]) -> dict[str, Any]:
	"""Set whether only the owner may post in a target group."""
	client, chat_id = _owned_client(bridge, payload)
	only_owner = payload["only_owner"]
	if not isinstance(only_owner, bool):
		raise ValueError("only_owner 必须是布尔值")
	checked(client.api("chats.PatchChatPostUserRequest", {
		"chatId": chat_id,
		"postType": "ONLY_ADMIN" if only_owner else "ANYONE",
	}))
	return {"status": "ok", "chat_id": chat_id, "only_owner": only_owner}


def invite(bridge, payload: dict[str, Any]) -> dict[str, Any]:
	"""Issue a seven-day invitation link for an owned target group."""
	client, chat_id = _owned_client(bridge, payload)
	result = checked(client.api("chats.PullChatLinkTokenRequest", {"chatId": chat_id, "expiredDay": 7}))
	return {
		"status": "ok",
		"chat_id": chat_id,
		"shared_url": result["sharedUrl"],
		"kou_ling": result["kouLing"],
		"expire_time": result["expireTime"],
	}


ROUTES = {
	"/target-groups/create": create,
	"/target-groups/post-permission": post_permission,
	"/target-groups/invite": invite,
}
