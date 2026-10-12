"""Which groups the personal session is in, and whether it is still in one.

Two questions the relay could not answer on its own: "which groups can be
bound" (so a route is created by picking a group, not by typing its name) and
"has the account left a bound group" (so a route does not sit there looking
healthy while nothing can arrive).  Both are read-only gateway calls; the
gateway access is passed in, so this module has no network or larkx
dependency and its rules can be tested directly.
"""

from __future__ import annotations

from typing import Any, Callable, Iterable

GROUP_CHAT_TYPE = 2
ROLE_MEMBER = 1
FEED_PAGE_SIZE = 50
MAX_FEED_PAGES = 40

PullFeedPage = Callable[[str, int | None], dict[str, Any]]


def _int(value: Any, default: int = 0) -> int:
	try:
		return int(value)
	except (TypeError, ValueError):
		return default


def _chat_type(value: Any) -> int:
	if isinstance(value, str) and not value.lstrip("-").isdigit():
		return GROUP_CHAT_TYPE if value.upper() == "GROUP" else 0
	return _int(value)


def groups_from_feed(chats: Any) -> list[dict[str, Any]]:
	"""The group chats of one feed page, as {chat_id, name, member_count, last_position}."""
	items = chats.items() if isinstance(chats, dict) else ((chat.get("id"), chat) for chat in chats or [] if isinstance(chat, dict))
	groups = []
	for chat_id, chat in items:
		chat_id = str(chat.get("id") or chat_id or "").strip()
		if not chat_id.isdigit() or _chat_type(chat.get("type")) != GROUP_CHAT_TYPE or chat.get("isDissolved"):
			continue
		groups.append({
			"chat_id": chat_id,
			"name": str(chat.get("name") or "").strip(),
			"member_count": _int(chat.get("memberCount") or chat.get("userCount")),
			"last_position": _int(chat.get("lastMessagePosition")),
		})
	return groups


def pull_joined_groups(pull_page: PullFeedPage, *, feeds: Iterable[str] = ("INBOX", "DONE"), max_pages: int = MAX_FEED_PAGES) -> list[dict[str, Any]]:
	"""Every group in the account's conversation list, the archived ("done") ones included.

	``pull_page(feed, cursor)`` returns one feed page; ``cursor`` is None for
	the first.  A group moved to "done" is still a group the account is in,
	so both lists are read.  Paging stops on an empty page or a cursor that
	does not move, and is capped so a misbehaving gateway cannot loop.
	"""
	seen: dict[str, dict[str, Any]] = {}
	for feed in feeds:
		cursor: int | None = None
		for _ in range(max_pages):
			page = pull_page(feed, cursor) or {}
			for group in groups_from_feed(page.get("chats")):
				seen.setdefault(group["chat_id"], group)
			next_cursor = _int(page.get("nextCursor"))
			if not page.get("cards") or not next_cursor or next_cursor == cursor:
				break
			cursor = next_cursor
	return sorted(seen.values(), key=lambda group: (group["name"], group["chat_id"]))


def membership_report(
	chat_ids: Iterable[str],
	*,
	roles: dict[str, Any],
	infos: dict[str, dict[str, Any]],
	confirm_in_chat: Callable[[str], bool],
) -> dict[str, dict[str, Any]]:
	"""Per chat: is the account a member, with the facts that decision rests on.

	``member`` is True, False or None.  The role list is the first answer;
	a chat it does not report as MEMBER is checked a second way before it is
	called gone, and any failure of that check yields None (unknown), never
	False: a route must not be marked as left because one call failed.
	"""
	report: dict[str, dict[str, Any]] = {}
	for chat_id in chat_ids:
		chat_id = str(chat_id)
		info = infos.get(chat_id) or {}
		role = roles.get(chat_id)
		dissolved = bool(info.get("isDissolved"))
		member: bool | None
		if dissolved:
			member = False
		elif role is not None and _int(role, -1) == ROLE_MEMBER:
			member = True
		else:
			try:
				member = bool(confirm_in_chat(chat_id))
			except Exception:
				member = None
		first = _int(info.get("firstChatMessagePosition"), 1)
		report[chat_id] = {
			"member": member,
			"role": None if role is None else _int(role, -1),
			"dissolved": dissolved,
			"name": str(info.get("name") or "").strip(),
			"member_count": _int(info.get("memberCount") or info.get("userCount")),
			"first_position": max(1, first),
			"last_position": _int(info.get("lastMessagePosition")),
		}
	return report
