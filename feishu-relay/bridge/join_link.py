"""Joining a group from an invitation link."""

from __future__ import annotations

from typing import Any
from urllib.parse import parse_qs, urlparse

from group_admin import checked, require_client


INVITE_HOST = "applink.feishu.cn"
INVITE_PATH = "/client/chat/chatter/add_by_link"
TOKEN_LENGTH = 36


def _token(link: Any) -> str:
	parsed = urlparse(str(link or "").strip())
	if parsed.scheme != "https" or parsed.netloc != INVITE_HOST or parsed.path != INVITE_PATH or parsed.params or parsed.fragment:
		raise ValueError("邀请链接必须是飞书群邀请链接")
	query = parse_qs(parsed.query, keep_blank_values=True)
	if set(query) != {"link_token"} or len(query["link_token"]) != 1:
		raise ValueError("邀请链接缺少有效的 link_token")
	token = query["link_token"][0]
	if len(token) != TOKEN_LENGTH:
		raise ValueError("邀请链接中的 link_token 无效")
	return token


def _chat_result(chat: dict[str, Any]) -> dict[str, Any]:
	return {"chat_id": str(chat["id"]), "name": str(chat["name"])}


def link_info(bridge, payload: dict[str, Any]) -> dict[str, Any]:
	token = _token(payload.get("link"))
	result = require_client(bridge).api("chats.PullChatLinkInfoRequest", {"token": token})
	chat = result["chat"]
	return {
		"status": "ok",
		**_chat_result(chat),
		"member_count": int(chat["userCount"]),
		"already_in_chat": bool(result["alreadyInChat"]),
		"expire_time": result["expireTime"],
	}


def join_by_link(bridge, payload: dict[str, Any]) -> dict[str, Any]:
	token = _token(payload.get("link"))
	result = checked(require_client(bridge).api("chats.PutChatterViaLinkRequest", {"token": token}))
	chat = result["chat"]
	# Successful joins were not observed; rely only on the declared {chat} response.
	bridge._joined_groups_cache = None
	return {"status": "ok", **_chat_result(chat)}


ROUTES = {
	"/chats/link-info": link_info,
	"/chats/join-by-link": join_by_link,
}
