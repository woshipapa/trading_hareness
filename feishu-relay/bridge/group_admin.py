"""Shared ownership and gateway response rules for group administration."""

from __future__ import annotations

from typing import Any


class GroupAdminError(Exception):
	"""A group administration request was refused."""


def require_owner(chat_info: dict[str, Any], user_id: str) -> None:
	"""Require that the account is the chat owner."""
	if str((chat_info or {}).get("ownerId") or "") != str(user_id):
		raise GroupAdminError("只有群主可以执行此操作")


def checked(result: dict[str, Any]) -> dict[str, Any]:
	"""Reject a failed gateway envelope and return a successful result."""
	if "sid" in result and result.get("payloadType") != 1:
		raise GroupAdminError(str(result["sid"]))
	return result
