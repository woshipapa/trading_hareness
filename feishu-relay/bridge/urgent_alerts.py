"""Send a short text and mark it urgent for a selected chat member."""

from __future__ import annotations

import time
import threading
from typing import Any, Callable, Iterable

from chat_directory import pull_joined_groups
from group_admin import GroupAdminError, checked, require_client
from send_gate import SendRateLimited


URGENT_WINDOW_SECONDS = 3600
URGENT_LIMIT = 6
_URGENT_LIMIT_LOCK = threading.Lock()


def other_member(member_ids: Iterable[str], account_id: str, user_id: str) -> bool:
	"""Return whether a two-member chat contains exactly the requested other member."""
	members = {str(member_id) for member_id in member_ids}
	return len(members) == 2 and str(account_id) in members and str(user_id) in members and str(user_id) != str(account_id)


def recipient_groups(groups: Iterable[dict[str, Any]], *, chat_infos: dict[str, dict[str, Any]], member_ids: Callable[[str], Iterable[str]], account_id: str) -> list[dict[str, str]]:
	"""Select joined two-person groups and expose the other member."""
	result = []
	for group in groups:
		chat_id = str(group["chat_id"])
		info = chat_infos.get(chat_id) or {}
		if int(info.get("userCount") or 0) != 2:
			continue
		members = {str(member_id) for member_id in member_ids(chat_id)}
		if len(members) != 2 or str(account_id) not in members:
			continue
		user_id = next(member_id for member_id in members if member_id != str(account_id))
		result.append({"chat_id": chat_id, "name": str(info.get("name") or group.get("name") or ""), "user_id": user_id})
	return result


def check_hourly_limit(timestamps: Iterable[float], now: float, *, limit: int = URGENT_LIMIT, window: int = URGENT_WINDOW_SECONDS) -> list[float]:
	"""Prune old admissions and reserve one slot, or raise the bridge's 429 error."""
	active = [float(timestamp) for timestamp in timestamps if float(timestamp) > now - window]
	if len(active) >= limit:
		oldest = min(active)
		raise SendRateLimited("urgent", max(1, int(oldest + window - now) + 1))
	return [*active, now]


def pick_sent_message(messages: Iterable[dict[str, Any]], account_id: str, text: str) -> str:
	"""Pick the only message authored by the account with the exact sent text."""
	items = list(messages.values()) if isinstance(messages, dict) else list(messages)
	owned = [message for message in items if str(message.get("from_id", message.get("fromId", ""))) == str(account_id) and str(message.get("content") or "") == text]
	if len(owned) != 1:
		raise RuntimeError("发送后未找到唯一的对应消息，未执行加急")
	message = owned[0]
	return str(message.get("msg_id") or message.get("message_id") or message.get("id") or "")


def pull_member_ids(client: Any, chat_id: str) -> set[str]:
	"""Read all member ids from the gateway's paged chatter response."""
	result: set[str] = set()
	cursor = 0
	while True:
		page = client.api("chats.PullChatChattersV2Request", {"chatId": str(chat_id), "limit": 100, "cursor": cursor}) or {}
		result.update(str(member_id) for member_id in (page.get("chatterJoinTimeSec") or {}))
		next_cursor = int(page.get("nextCursor") or 0)
		if not next_cursor or next_cursor == cursor:
			return result
		cursor = next_cursor


def _chat_last_position(client: Any, chat_id: str) -> int:
	info = client.chat_infos([str(chat_id)]).get(str(chat_id)) or {}
	return int(info.get("lastMessagePosition") or 0)


def _send_message_id(client: Any, chat_id: str, account_id: str, text: str, before_position: int) -> str:
	after_position = _chat_last_position(client, chat_id)
	positions = list(range(before_position + 1, after_position + 1))
	return pick_sent_message(client.pull_history(str(chat_id), positions), account_id, text)


def _decode_gateway_response(raw: bytes) -> dict[str, Any]:
	from larkx.proto import proto_pb2

	packet = proto_pb2.Packet()
	packet.ParseFromString(raw)
	return {"payloadType": int(packet.payloadType), "sid": str(packet.sid)}


def checked_send_response(response: Any, decode: Callable[[bytes], dict[str, Any]] = _decode_gateway_response) -> dict[str, Any]:
	envelope = response if isinstance(response, dict) else decode(response)
	if int(envelope.get("payloadType") or 0) != 1:
		raise RuntimeError(f"飞书网关拒绝发送：{envelope.get('sid') or '未知原因'}")
	return envelope


def reserve_urgent_slot(bridge: Any, now: float) -> float:
	"""Reserve a slot atomically; the in-memory window resets when the bridge restarts."""
	with _URGENT_LIMIT_LOCK:
		active = check_hourly_limit(getattr(bridge, "_urgent_alert_timestamps", []), now)
		bridge._urgent_alert_timestamps = active
	return now


def release_urgent_slot(bridge: Any, timestamp: float) -> None:
	with _URGENT_LIMIT_LOCK:
		remaining = list(getattr(bridge, "_urgent_alert_timestamps", []))
		remaining.remove(timestamp)
		bridge._urgent_alert_timestamps = remaining


def resolve_recipients(bridge: Any, _payload: dict[str, Any]) -> dict[str, Any]:
	client = require_client(bridge)
	account_id = str(bridge.auth.user_id)
	groups = pull_joined_groups(client.feed_page)
	chat_ids = [group["chat_id"] for group in groups]
	infos = client.chat_infos(chat_ids)
	return {"status": "ok", "recipients": recipient_groups(groups, chat_infos=infos, member_ids=lambda chat_id: pull_member_ids(client, chat_id), account_id=account_id)}


def send_urgent(bridge: Any, payload: dict[str, Any], *, build_packet: Callable[..., Any], request_id: Callable[[], str]) -> dict[str, Any]:
	client = require_client(bridge)
	chat_id = str(payload.get("chat_id") or "").strip()
	user_id = str(payload.get("user_id") or "").strip()
	text = str(payload.get("text") or "")
	if not chat_id or not user_id:
		raise ValueError("chat_id 和 user_id 不能为空")
	if not text or len(text) > 500:
		raise ValueError("text 不能为空且不能超过 500 个字符")
	account_id = str(bridge.auth.user_id)
	members = pull_member_ids(client, chat_id)
	if not other_member(members, account_id, user_id):
		raise GroupAdminError("接收会话必须恰好只有本账号和接收人")

	reservation = reserve_urgent_slot(bridge, time.time())
	audit_id = None
	try:
		admitted = bridge.send_gate.begin(chat_id=chat_id, text=text, root_id=None, idempotency_key=None)
		audit_id = admitted["audit_id"]
		before_position = _chat_last_position(client, chat_id)
		packet = build_packet(text, chat_id, request_id())
		checked_send_response(client._gateway_post(packet))
		message_id = _send_message_id(client, chat_id, account_id, text, before_position)
		if not message_id:
			raise RuntimeError("未找到刚发送的消息")
		checked(client.api("urgent.PutUrgentRequest", {"messageId": message_id, "chatterIds": [user_id], "urgentType": "APP", "chatId": chat_id}))
	except Exception as error:
		release_urgent_slot(bridge, reservation)
		if audit_id is not None:
			bridge.send_gate.finish(audit_id, ok=False, error=str(error))
		raise
	bridge.send_gate.finish(audit_id, ok=True)
	return {"status": "ok", "message_id": message_id, "urgent": True}


def urgent(bridge: Any, payload: dict[str, Any]) -> dict[str, Any]:
	from larkx.proto.builders import build_send_message_packet
	from larkx.proto.ids import generate_long_request_id

	return send_urgent(bridge, payload, build_packet=build_send_message_packet, request_id=generate_long_request_id)


ROUTES = {"/alerts/recipients": resolve_recipients, "/alerts/urgent": urgent}
