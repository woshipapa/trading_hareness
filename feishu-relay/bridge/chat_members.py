"""Manage members and join requests in groups owned by the relay account."""

from __future__ import annotations

from typing import Any

from group_admin import GroupAdminError, checked, require_client, require_owner


MEMBER_PAGE_SIZE = 100
HISTORY_PAGE_SIZE = 20
APPLICATION_PAGE_SIZE = 50


def _chat_id(payload: dict[str, Any]) -> str:
	chat_id = str(payload.get("chat_id", "")).strip()
	if not chat_id.isdigit():
		raise ValueError("chat_id 必须是数字 WebSocket 群 ID")
	return chat_id


def _owned(bridge, chat_id: str):
	client = require_client(bridge)
	info = (client.chat_infos([chat_id]) or {}).get(chat_id) or {}
	require_owner(info, str(bridge.auth.user_id))
	return client


def _names(bridge, chat_id: str, user_ids: list[str]) -> dict[str, str]:
	return bridge.name_cache.resolve(chat_id, user_ids, bridge.client.get_user_name)


def list_members(bridge, payload: dict[str, Any]) -> dict[str, Any]:
	chat_id = _chat_id(payload)
	client = _owned(bridge, chat_id)
	joined: dict[str, Any] = {}
	cursor = None
	while True:
		request = {"chatId": chat_id, "limit": MEMBER_PAGE_SIZE}
		if cursor is not None:
			request["cursor"] = cursor
		page = checked(client.api("chats.PullChatChattersV2Request", request))
		for user_id, joined_at in (page.get("chatterJoinTimeSec") or {}).items():
			joined.setdefault(str(user_id), joined_at)
		next_cursor = int(page.get("nextCursor") or 0)
		if not next_cursor:
			break
		cursor = next_cursor
	user_ids = list(joined)
	names = _names(bridge, chat_id, user_ids)
	return {"status": "ok", "chat_id": chat_id, "members": [
		{"user_id": user_id, "name": names.get(user_id, ""), "joined_at": int(joined_at)}
		for user_id, joined_at in joined.items()
	]}


def history(bridge, payload: dict[str, Any]) -> dict[str, Any]:
	chat_id = _chat_id(payload)
	client = _owned(bridge, chat_id)
	request = {"chatId": chat_id, "count": HISTORY_PAGE_SIZE}
	if payload.get("cursor") is not None:
		request["cursor"] = payload["cursor"]
	page = checked(client.api("chats.PullChatJoinLeaveHistoryRequest", request))
	items = page.get("chatterHistory") or []
	user_ids = [str(item.get("chatterId") or "") for item in items]
	names = _names(bridge, chat_id, user_ids)
	rows = []
	for item, user_id in zip(items, user_ids):
		rows.append({
			"user_id": user_id,
			"name": names.get(user_id, ""),
			"action": {1: "join", 2: "leave"}.get(int(item.get("action") or 0), "unknown"),
			"event": item.get("event"),
			"chatter_type": item.get("chatterType"),
			"at": item.get("createTime"),
		})
	return {
		"status": "ok", "chat_id": chat_id, "rows": rows,
		"next_cursor": page.get("nextCursor") if page.get("hasMore") else None,
	}


def applications(bridge, payload: dict[str, Any]) -> dict[str, Any]:
	chat_id = _chat_id(payload)
	client = _owned(bridge, chat_id)
	page = checked(client.api("chats.PullChatApplicationsRequest", {
		"type": "JOIN_CHAT", "isDescOrder": True, "count": APPLICATION_PAGE_SIZE, "chatId": chat_id,
	}))
	return {"status": "ok", "chat_id": chat_id, "applications": [
		{
			"id": item.get("id"),
			"status": item.get("status"),
			"apply_time": item.get("applyTime"),
			"message": item.get("extraMessage"),
			"contact_summary": item.get("contactSummary"),
		}
		for item in page.get("applications") or []
	]}


def decide_application(bridge, payload: dict[str, Any]) -> dict[str, Any]:
	chat_id = _chat_id(payload)
	client = _owned(bridge, chat_id)
	application_id = payload.get("id")
	agree = payload.get("agree") is True
	checked(client.api("chats.ProcessChatApplicationRequest", {
		"id": application_id, "status": "AGREED" if agree else "REFUSED",
	}))
	return {"status": "ok", "chat_id": chat_id, "id": application_id, "agree": agree}


def remove_member(bridge, payload: dict[str, Any]) -> dict[str, Any]:
	chat_id = _chat_id(payload)
	client = _owned(bridge, chat_id)
	user_id = str(payload.get("user_id", "")).strip()
	if user_id == str(bridge.auth.user_id):
		raise GroupAdminError("不能移除本账号")
	checked(client.api("chats.DeleteChatChatterRequest", {
		"chatId": chat_id, "chatterIds": [user_id],
	}))
	return {"status": "ok", "chat_id": chat_id, "user_id": user_id}


ROUTES = {
	"/chat-members/list": list_members,
	"/chat-members/history": history,
	"/chat-members/applications": applications,
	"/chat-members/applications/decide": decide_application,
	"/chat-members/remove": remove_member,
}
