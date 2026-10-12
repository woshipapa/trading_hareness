"""Faithful text for a Feishu RichText element tree, links included.

The pinned upstream ``extract_rich_text`` skips anchor elements, so a TEXT or
POST message loses every link ("链接:  提取码: q7sx").  This walks the element
tree from the raw protobuf property bytes, which only exist before
``json_safe`` turns them into replacement-character strings, and keeps each
link at the position it was typed.

Pure and dependency-free (no larkx): the property messages are read with the
same minimal wire reader the image extractor uses.
"""

from __future__ import annotations

import re
from typing import Any

from larkagentx_image_property import _fields

TAG_TEXT, TAG_IMG, TAG_P, TAG_FIGURE, TAG_AT, TAG_A = 1, 2, 3, 4, 5, 6
TAG_B, TAG_I, TAG_U, TAG_EMOTION, TAG_DIV, TAG_LINK, TAG_MEDIA, TAG_DOCS = 7, 8, 9, 10, 14, 17, 18, 22

_INLINE_TEXT_TAGS = {TAG_TEXT, TAG_B, TAG_I, TAG_U}
_BLOCK_TAGS = {TAG_P, TAG_FIGURE, TAG_DIV}
# Same placeholders as the upstream summary, so downstream stripping still applies.
_PLACEHOLDERS = {TAG_IMG: "[图片]", TAG_EMOTION: "[表情]", TAG_MEDIA: "[视频]", TAG_DOCS: "[文档]"}
# The private Feishu resource CDN (stickers, inline images) must never be
# relayed as a link; any shard (s1, s3, ...) counts.
_PRIVATE_CDN_RE = re.compile(r"^https?://s\d+-imfile\.feishucdn\.com/", re.IGNORECASE)
_EXTERNAL_URL_RE = re.compile(r"^https?://", re.IGNORECASE)
MAX_RENDERED_CHARS = 20_000
_URL_TERMINATORS = set("，。！？；：、）)】》」』,.!?;:<>\"'")
_MAX_DEPTH = 32


def _string_fields(property_bytes: Any) -> dict[int, str]:
	"""Length-delimited fields of a property message as text, first value per field."""
	if not isinstance(property_bytes, (bytes, bytearray)) or not property_bytes:
		return {}
	found: dict[int, str] = {}
	try:
		for number, wire_type, value in _fields(bytes(property_bytes)):
			if wire_type == 2 and number not in found:
				found[number] = bytes(value).decode("utf-8", errors="replace")
	except ValueError:
		return {}
	return found


def is_relayable_url(url: str) -> bool:
	return bool(_EXTERNAL_URL_RE.match(url)) and not _PRIVATE_CDN_RE.match(url)


def render_rich_text(rich_text: Any) -> dict[str, Any]:
	"""Return ``{"text": str, "links": [str]}`` for a RichText dict that still holds bytes."""
	empty = {"text": "", "links": []}
	if not isinstance(rich_text, dict):
		return empty
	dictionary = (rich_text.get("elements") or {}).get("dictionary")
	if not isinstance(dictionary, dict) or not dictionary:
		return empty
	root_ids = [str(item) for item in (rich_text.get("elementIds") or []) if str(item) in dictionary]
	if not root_ids:
		# No declared order: fall back to the upstream rule (numeric ids ascending).
		try:
			root_ids = sorted((str(key) for key in dictionary), key=int)
		except ValueError:
			root_ids = [str(key) for key in dictionary]
	chunks: list[str] = []
	links: list[str] = []
	seen: set[str] = set()
	after_link = False

	def append(text: str, *, is_link: bool = False) -> None:
		# "…/s/abc查看" would read as one URL downstream; separate a link from
		# text glued to its end, but leave punctuation and whitespace alone.
		nonlocal after_link
		if not text:
			return
		if after_link and not text[0].isspace() and text[0] not in _URL_TERMINATORS:
			chunks.append(" ")
		chunks.append(text)
		after_link = is_link

	def emit_link(label: str, href: str) -> None:
		href = href.strip()
		label = label.strip()
		if href and not is_relayable_url(href):
			href = ""
			if _PRIVATE_CDN_RE.match(label) or not label:
				return  # a sticker/inline-image anchor carries no readable content
		if href and href not in links:
			links.append(href)
		if href and label and label != href and href not in label:
			append(f"{label} {href}", is_link=True)
		else:
			append(label if (label and href in label) else (href or label), is_link=bool(href))

	def visit(node_id: str, depth: int) -> None:
		if node_id in seen or depth > _MAX_DEPTH:  # malformed cycles or absurd nesting
			return
		seen.add(node_id)
		node = dictionary.get(node_id)
		if not isinstance(node, dict):
			return
		try:
			tag = int(node.get("tag") or TAG_TEXT)
		except (TypeError, ValueError):
			tag = 0
		fields = _string_fields(node.get("property"))
		children = [str(child) for child in (node.get("childIds") or [])]
		if tag == TAG_A:
			emit_link(fields.get(2) or fields.get(3) or "", fields.get(1) or "")
			return
		if tag == TAG_LINK:
			start = len(chunks)
			for child in children:
				visit(child, depth + 1)
			label = "".join(chunks[start:])
			del chunks[start:]
			after_link = False
			emit_link(label, fields.get(1) or "")
			return
		if tag == TAG_AT:
			append(fields.get(2) or "[@]")
			return
		if tag in _PLACEHOLDERS:
			append(_PLACEHOLDERS[tag])
			return
		if tag in _INLINE_TEXT_TAGS or tag in _BLOCK_TAGS:
			append(fields.get(1) or "")
		for child in children:
			visit(child, depth + 1)
		if tag in _BLOCK_TAGS and chunks and not chunks[-1].endswith("\n"):
			append("\n")

	for root_id in root_ids:
		visit(root_id, 0)
	text = "".join(chunks).replace("\r\n", "\n").replace("\r", "\n")
	# Empty paragraphs (and the blank left by a dropped sticker anchor) collapse
	# to at most one blank line.
	text = re.sub(r"[^\S\n]+\n", "\n", text)  # any horizontal whitespace, NBSP included
	text = re.sub(r"\n{3,}", "\n\n", text).strip("\n")
	return {"text": text[:MAX_RENDERED_CHARS], "links": links}
