"""Observe relay target messages for delivery reconciliation."""

from __future__ import annotations

import json
import logging
import threading
from typing import Any
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, urlopen

from group_admin import checked, require_client, require_owner


LOG = logging.getLogger("larkagentx-bridge.target-observer")


def _observed(bridge) -> dict[str, set[str]]:
	return getattr(bridge, "_target_observer_chats", {})


def observe(bridge, payload: dict[str, Any]) -> dict[str, Any]:
	observed: dict[str, set[str]] = {}
	for item in payload.get("chats", []):
		chat_id = str(item.get("chat_id", "")).strip()
		if not chat_id:
			continue
		observed[chat_id] = {str(bot_id).strip() for bot_id in item.get("bot_ids", []) if str(bot_id).strip()}
	bridge._target_observer_chats = observed
	return {"status": "ok", "observed": len(observed)}


def _observed_url(bridge) -> str:
	parts = urlsplit(bridge.ingress_url)
	return urlunsplit((parts.scheme, parts.netloc, "/internal/larkagentx/target-observed", "", ""))


def _post_observed(bridge, payload: dict[str, Any]) -> None:
	request = Request(_observed_url(bridge), data=json.dumps(payload, ensure_ascii=False).encode("utf-8"), method="POST", headers={
		"content-type": "application/json", "x-larkagentx-token": bridge.token,
	})
	try:
		with urlopen(request, timeout=10) as response:
			response.read(64 * 1024)
	except Exception as error:
		LOG.warning("目标群消息旁路上报失败：%s", error)


def tap(bridge, chat_id: str, message: dict[str, Any]) -> None:
	chat_id = str(chat_id)
	bot_ids = _observed(bridge).get(chat_id)
	if bot_ids is None or str(message.get("from_id", "")) not in bot_ids:
		return
	payload = {
		"chat_id": chat_id,
		"message_id": message.get("msg_id"),
		"position": message.get("position"),
		"create_time": message.get("create_time"),
		"from_id": message.get("from_id"),
		"msg_type": message.get("msg_type_name", message.get("msg_type")),
		"text": message.get("content", ""),
	}
	threading.Thread(target=_post_observed, args=(bridge, payload), daemon=True).start()


def recall_in_group(bridge, payload: dict[str, Any]) -> dict[str, Any]:
	chat_id = str(payload.get("chat_id", "")).strip()
	message_id = str(payload.get("message_id", "")).strip()
	client = require_client(bridge)
	info = client.chat_infos([chat_id]).get(chat_id) or {}
	require_owner(info, str(bridge.auth.user_id))
	checked(client.api("messages.DeleteGroupMessageRequest", {"id": message_id}))
	return {"status": "ok", "chat_id": chat_id, "message_id": message_id}


ROUTES = {
	"/targets/observe": observe,
	"/messages/recall-in-group": recall_in_group,
}
