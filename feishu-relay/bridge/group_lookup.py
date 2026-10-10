"""Resolve a group name to its WebSocket (numeric) chat id.

The gateway search returns highlighted titles (``<h>name</h>``) for every
group that merely contains the query.  Binding must be exact and unique, or a
dashboard-typed name could subscribe the relay to the wrong group.  Kept free
of bridge/larkx imports so it is testable anywhere.
"""

from __future__ import annotations

import re
from typing import Any, Iterable

_TAG_RE = re.compile(r"</?[A-Za-z][^>]*>")


class GroupLookupError(ValueError):
	"""The name cannot be bound safely (missing, ambiguous or conflicting)."""


def strip_highlight(title: Any) -> str:
	return _TAG_RE.sub("", str(title or "")).strip()


def unique_group_id(results: Iterable[Any] | None, name: str) -> str:
	wanted = str(name or "").strip()
	if not wanted:
		raise GroupLookupError("群名不能为空")
	ids: list[str] = []
	for item in results or []:
		if not isinstance(item, dict) or item.get("type") != "group":
			continue
		if strip_highlight(item.get("title")) != wanted:
			continue
		chat_id = str(item.get("id") or "").strip()
		if chat_id.isdigit() and chat_id not in ids:
			ids.append(chat_id)
	if not ids:
		raise GroupLookupError(f"未找到名为「{wanted}」的群（只能解析本账号已加入的群）")
	if len(ids) > 1:
		raise GroupLookupError(f"有 {len(ids)} 个同名群，拒绝自动绑定：{wanted}")
	return ids[0]
