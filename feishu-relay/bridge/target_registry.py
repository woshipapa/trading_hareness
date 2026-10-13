"""Resolve existing webhook target groups from the logged-in web session."""

from __future__ import annotations

import hashlib
from typing import Any

from chat_bots import custom_bots
from chat_directory import pull_joined_groups
from group_admin import require_client


def _webhook_hash(info: dict[str, Any], requested: set[str]) -> str | None:
	if info.get("no_permission"):
		return None
	webhook = str(info.get("webhook") or "")
	if not webhook:
		return None
	digest = hashlib.sha256(webhook.encode("utf-8")).hexdigest()
	return digest if digest in requested else None


def resolve_groups(groups: list[dict[str, Any]], *, chat_infos: dict[str, dict[str, Any]], pull_chat_bots,
	                   chatbot_api, user_id: str, requested: set[str]) -> list[dict[str, Any]]:
	"""Transform joined groups and injected bot lookups into the public result."""
	resolved = []
	for group in groups:
		chat_id = group["chat_id"]
		chat_info = chat_infos.get(chat_id) or {}
		bots = []
		for bot in custom_bots(pull_chat_bots(chat_id)):
			info = chatbot_api("GetWebhookBotInfo", {"bot_id": bot["bot_id"], "i18n": "zh-CN"})
			data = info["data"] if info["code"] == 0 else {"no_permission": True}
			entry = {
				"bot_id": bot["bot_id"],
				"name": str(data.get("name") or bot["name"]),
				"key_words": list(data.get("key_words") or []),
			}
			webhook_hash = _webhook_hash(data, requested)
			if webhook_hash:
				entry["webhook_hash"] = webhook_hash
			bots.append(entry)
		resolved.append({
			"chat_id": chat_id,
			"name": group["name"],
			"owned": str(chat_info.get("ownerId") or "") == str(user_id),
			"bots": bots,
		})
	return resolved


def resolve(bridge, payload: dict[str, Any]) -> dict[str, Any]:
	"""List every joined group and expose only requested webhook hashes."""
	requested = {str(value).strip().lower() for value in payload.get("webhook_hashes", [])}
	client = require_client(bridge)
	groups = pull_joined_groups(client.feed_page)
	chat_ids = [group["chat_id"] for group in groups]
	infos = client.chat_infos(chat_ids)
	return {"groups": resolve_groups(groups, chat_infos=infos, pull_chat_bots=client.chat_bots,
	                                  chatbot_api=client.chatbot_api, user_id=str(bridge.auth.user_id), requested=requested)}


ROUTES = {"/targets/resolve": resolve}
