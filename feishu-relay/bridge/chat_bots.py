"""A group's custom bot (the one that owns a webhook), through the web client's own interfaces.

Feishu has no published API for adding a custom bot to a group.  The web
client does it with what is used here: two gateway commands that list a
group's bots and the bots that can be added to it, and a handful of HTTP calls
under ``open-apis/chatbot/api`` that create, read, edit and remove a custom
bot, authenticated by the session cookies alone.  Observed 2026-10-13 in the
web client's ``openplatform`` module (``GroupBotModal``) and tried once with
the relay's own session: create, set a keyword, send, delete.

The relay uses it so that a new target group needs no hand-made bot.  Network
access is passed in, so the rules can be tested without a session.  A webhook
address never appears in an error raised here.
"""

from __future__ import annotations

import re
from typing import Any, Callable

PULL_CHAT_BOTS_CMD = 13100
PULL_CHAT_CANDIDATE_BOTS_CMD = 13101
CUSTOM_BOT_COLLECTION = 1
DEFAULT_BOT_NAME = "转发机器人"
DEFAULT_BOT_DESCRIPTION = "消息转发用，由转发看板自动创建"
MAX_KEYWORDS = 10
LOCALE = "zh-CN"
WEBHOOK_PATTERN = re.compile(r"https://open\.(?:feishu\.cn|larksuite\.com)/open-apis/bot/v2/hook/[A-Za-z0-9-]{8,80}")

Call = Callable[[str, dict[str, Any]], dict[str, Any]]
Pull = Callable[[str], dict[str, Any]]


class ChatBotError(Exception):
	"""The group's bot could not be listed, created, read, changed or removed."""


def custom_bots(response: dict[str, Any]) -> list[dict[str, str]]:
	"""The custom bots in a PullChatBots answer, as {bot_id, name, creator_id}."""
	found = []
	for collection in (response or {}).get("botCollection") or []:
		if int(collection.get("type") or 0) != CUSTOM_BOT_COLLECTION:
			continue
		for key, bot in (collection.get("bots") or {}).items():
			found.append({"bot_id": str(bot.get("id") or key), "name": str(bot.get("name") or ""), "creator_id": str(bot.get("creatorId") or "")})
	return found


def bot_count(response: dict[str, Any]) -> int:
	return sum(len(collection.get("bots") or {}) for collection in (response or {}).get("botCollection") or [])


def custom_bot_template(response: dict[str, Any]) -> dict[str, str]:
	"""The "custom bot" entry of a PullChatCandidateBots answer: what a new one is created from."""
	templates = list(((response or {}).get("functionalBots") or {}).values())
	named = [bot for bot in templates if str(bot.get("enName") or "").strip().lower() == "custom bot"]
	chosen = (named or templates or [None])[0]
	if not chosen or not chosen.get("id") or not (chosen.get("avatar") or {}).get("key"):
		raise ChatBotError("这个群不能添加自定义机器人（群设置不允许，或账号没有权限）")
	return {"id": str(chosen["id"]), "avatar_key": str(chosen["avatar"]["key"])}


def _checked(name: str, answer: Any) -> dict[str, Any]:
	if isinstance(answer, dict) and answer.get("code") == 0:
		return answer.get("data") or {}
	reason = (answer.get("msg") or answer.get("code")) if isinstance(answer, dict) else None
	raise ChatBotError(f"{name}被拒绝：{reason or '无效响应'}")


def ensure_custom_bot(*, chat_id: str, keyword: str, user_id: str, pull_chat_bots: Pull, pull_candidate_bots: Pull, call: Call,
                      name: str = DEFAULT_BOT_NAME, description: str = DEFAULT_BOT_DESCRIPTION) -> dict[str, Any]:
	"""The webhook of the group's relay bot, creating the bot when the group has none.

	The bot is recognised by its name and its creator, so a second call reuses
	it instead of adding another, and a bot somebody else made is never
	touched.  Its security setting is the keyword: the relay does not sign its
	messages, so signing and the IP list are switched off on this bot.
	"""
	chat_id, keyword, name = str(chat_id).strip(), str(keyword or "").strip(), str(name or "").strip()
	if not chat_id.isdigit():
		raise ValueError("chat_id 必须是数字 WebSocket 群 ID")
	if not keyword or len(keyword) > 40:
		raise ValueError("关键词必须是 1–40 个字")
	if not name or len(name) > 30:
		raise ValueError("机器人名称必须是 1–30 个字")
	listing = pull_chat_bots(chat_id)
	own = [bot for bot in custom_bots(listing) if bot["name"] == name and bot["creator_id"] == str(user_id)]
	created = False
	if own:
		bot_id = own[0]["bot_id"]
	else:
		limit = int((listing or {}).get("maxBotNum") or 0)
		if limit and bot_count(listing) >= limit:
			raise ChatBotError(f"这个群的机器人已经到上限（{limit} 个），请先在飞书里移除一个")
		template = custom_bot_template(pull_candidate_bots(chat_id))
		data = _checked("创建机器人", call("CreateWebhookBot", {
			"name": name, "avatar_key": template["avatar_key"], "avatar_type": 0, "description": description,
			"bot_type_id": template["id"], "chat_id": chat_id, "user_id": str(user_id), "i18n": LOCALE,
		}))
		bot_id = str(data.get("bot_id") or "")
		if not bot_id:
			raise ChatBotError("创建机器人没有返回机器人编号")
		created = True
	info = _checked("读取机器人", call("GetWebhookBotInfo", {"bot_id": bot_id, "i18n": LOCALE}))
	if info.get("no_permission"):
		raise ChatBotError("账号没有权限读取这个机器人的设置")
	keywords = [str(word) for word in info.get("key_words") or [] if str(word).strip()]
	if keyword not in keywords or info.get("signature_status") or info.get("allow_ips") or not info.get("enable"):
		wanted = (keywords + [keyword] if keyword not in keywords else keywords)[-MAX_KEYWORDS:]
		_checked("设置机器人关键词", call("UpdateWebhookBotInfo", {
			"avatar_key": info.get("avatar_key"), "avatar_type": info.get("avatar_type") or 0, "name": info.get("name") or name,
			"description": info.get("description") or description, "enable": True, "check_mender": bool(info.get("check_mender")),
			"signature_status": False, "allow_ips": [], "key_words": wanted, "bot_id": bot_id, "i18n": LOCALE,
		}))
		info = _checked("读取机器人", call("GetWebhookBotInfo", {"bot_id": bot_id, "i18n": LOCALE}))
		if keyword not in (info.get("key_words") or []):
			raise ChatBotError("关键词没有设置成功")
	webhook = str(info.get("webhook") or "")
	if not WEBHOOK_PATTERN.fullmatch(webhook):
		raise ChatBotError("机器人没有返回可用的 webhook 地址")
	return {"chat_id": chat_id, "bot_id": bot_id, "name": str(info.get("name") or name), "keyword": keyword, "webhook": webhook, "created": created}


def remove_custom_bot(*, chat_id: str, bot_id: str, user_id: str, pull_chat_bots: Pull, call: Call, name: str = DEFAULT_BOT_NAME) -> dict[str, Any]:
	"""Remove the relay's own bot from a group; a bot it did not make is left alone."""
	chat_id, bot_id = str(chat_id).strip(), str(bot_id).strip()
	if not chat_id.isdigit() or not bot_id.isdigit():
		raise ValueError("chat_id 和 bot_id 必须是数字")
	bots = {bot["bot_id"]: bot for bot in custom_bots(pull_chat_bots(chat_id))}
	bot = bots.get(bot_id)
	if bot is None:
		return {"chat_id": chat_id, "bot_id": bot_id, "status": "not_found"}
	if bot["name"] != name or bot["creator_id"] != str(user_id):
		raise ChatBotError("这个机器人不是转发看板创建的，不自动移除")
	_checked("移除机器人", call("DeleteBotResource", {"chat_id": chat_id, "bot_id": bot_id, "i18n": LOCALE}))
	return {"chat_id": chat_id, "bot_id": bot_id, "status": "removed"}
