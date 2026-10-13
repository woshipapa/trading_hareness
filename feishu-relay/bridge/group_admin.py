"""Shared ownership and gateway response rules for group administration."""

from __future__ import annotations

from typing import Any


class GroupAdminError(Exception):
	"""A group administration request was refused."""


def gateway_error(response: Any, decoder, redactions: tuple[str, ...] = ()) -> GroupAdminError:
	"""Build the feature-facing error from a gateway response body."""
	status = getattr(response, "status_code", "unknown")
	try:
		packet = decoder(response.content)
		sid = str(getattr(packet, "sid", "") or "")
	except Exception:
		sid = ""
	message = sid or f"网关返回 HTTP {status}"
	for secret in redactions:
		if secret:
			message = message.replace(secret, "[已隐藏]")
	return GroupAdminError(message)


class _FeatureClient:
	"""Delegate the client, translating refused feature commands only."""

	def __init__(self, client):
		self._client = client

	def __getattr__(self, name):
		return getattr(self._client, name)

	def api(self, *args, **kwargs):
		try:
			return self._client.api(*args, **kwargs)
		except Exception as error:
			try:
				from requests import HTTPError
			except ModuleNotFoundError:
				raise error

			if not isinstance(error, HTTPError):
				raise
			from larkx.proto import proto_pb2

			def decode(body):
				packet = proto_pb2.Packet()
				packet.ParseFromString(body)
				return packet

			payload = args[1] if len(args) > 1 else kwargs.get("payload")
			token = str(payload.get("token") or "") if isinstance(payload, dict) else ""
			# Gateway reasons can echo invitation tokens, so redact them at the boundary.
			raise gateway_error(error.response, decode, (token,)) from error


def require_client(bridge):
	"""Return the logged-in client or report the bridge's login boundary."""
	if bridge.client is None:
		raise RuntimeError("尚未登录(needs_login),无法读取群列表")
	return _FeatureClient(bridge.client)


def require_owner(chat_info: dict[str, Any], user_id: str) -> None:
	"""Require that the account is the chat owner."""
	if str((chat_info or {}).get("ownerId") or "") != str(user_id):
		raise GroupAdminError("只有群主可以执行此操作")


def checked(result: dict[str, Any]) -> dict[str, Any]:
	"""Reject a failed gateway envelope and return a successful result."""
	if "sid" in result and result.get("payloadType") != 1:
		raise GroupAdminError(str(result["sid"]))
	return result
