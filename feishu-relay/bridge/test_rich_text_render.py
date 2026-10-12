import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from rich_text_render import is_relayable_url, render_rich_text


def varint(value):
	out = bytearray()
	while True:
		byte = value & 0x7F
		value >>= 7
		out.append(byte | (0x80 if value else 0))
		if not value:
			return bytes(out)


def field(number, text):
	data = text.encode("utf-8")
	return varint((number << 3) | 2) + varint(len(data)) + data


def text_node(content, tag=1, children=None):
	node = {"tag": tag, "property": field(1, content) if content else b""}
	if children:
		node["childIds"] = children
	return node


def anchor(href, label=None, tag=6):
	label = href if label is None else label
	return {"tag": tag, "property": field(1, href) + field(2, label) + field(3, label) + b"\x20\x00"}


BAIDU = "https://pan.baidu.com/s/1-g1gEqFhrgNlFejflFZAwA?pwd=q7sx"
# A URL longer than 127 bytes forces the two-byte length varint that json_safe
# later corrupts; here it must still decode exactly.
STICKER = "https://s3-imfile.feishucdn.com/static-resource/v1/v3_00160_00f38314-242b-44ff-b79f-0c20bda7656g~?image_size=noop&cut_type=&quality=&format=image&sticker_format=.webp"


class RichTextRenderTests(unittest.TestCase):
	def test_flat_text_message_keeps_the_link_where_it_was_typed(self):
		# Shape observed 2026-10-11: TEXT, flat elementIds, one anchor between two text runs.
		rich = {"anchorIds": ["a-1"], "elementIds": ["t1", "nl", "a-0", "a-1", "a-2"], "elements": {"dictionary": {
			"a-0": text_node("链接: "), "a-1": anchor(BAIDU), "a-2": text_node(" 提取码: q7sx"),
			"t1": text_node("通过网盘分享的文件：2026.10.11 安强内部vip.mp4"), "nl": text_node("\n"),
		}}}
		result = render_rich_text(rich)
		self.assertEqual(result["text"], f"通过网盘分享的文件：2026.10.11 安强内部vip.mp4\n链接: {BAIDU} 提取码: q7sx")
		self.assertEqual(result["links"], [BAIDU])

	def test_post_paragraphs_follow_the_tree_and_drop_private_cdn_anchors(self):
		rich = {"elementIds": ["p1", "p2", "p3", "p4"], "elements": {"dictionary": {
			"z-sticker": anchor(STICKER), "y-link": anchor("https://pan.baidu.com/s/1Skefd7vwcGD5xBESLj_gJw"),
			"p1": {"tag": 3, "property": b"", "childIds": ["z-sticker", "c1"]}, "c1": text_node("通过百度网盘分享的文件：10.11 安强…"),
			"p2": {"tag": 3, "property": b"", "childIds": ["c2", "y-link"]}, "c2": text_node("链接:"),
			"p3": {"tag": 3, "property": b"", "childIds": ["c3"]}, "c3": text_node("提取码:w12g "),
			"p4": {"tag": 3, "property": b"", "childIds": ["c4"]}, "c4": text_node("复制这段内容打开「百度网盘APP 即可获取」"),
		}}}
		result = render_rich_text(rich)
		self.assertEqual(result["text"], "通过百度网盘分享的文件：10.11 安强…\n链接:https://pan.baidu.com/s/1Skefd7vwcGD5xBESLj_gJw\n提取码:w12g\n复制这段内容打开「百度网盘APP 即可获取」")
		self.assertEqual(result["links"], ["https://pan.baidu.com/s/1Skefd7vwcGD5xBESLj_gJw"])
		self.assertNotIn("feishucdn", result["text"])

	def test_labelled_anchor_and_link_container_keep_label_and_target(self):
		rich = {"elementIds": ["a", "sep", "l"], "elements": {"dictionary": {
			"a": anchor("https://example.com/report", "研报原文"), "sep": text_node("；"),
			"l": {"tag": 17, "property": field(1, "https://example.com/live"), "childIds": ["lt"]}, "lt": text_node("点开观看"),
		}}}
		result = render_rich_text(rich)
		self.assertEqual(result["text"], "研报原文 https://example.com/report；点开观看 https://example.com/live")
		self.assertEqual(result["links"], ["https://example.com/report", "https://example.com/live"])

	def test_text_glued_to_the_end_of_a_link_is_separated_but_punctuation_is_not(self):
		rich = {"elementIds": ["t0", "a", "t1", "b", "t2"], "elements": {"dictionary": {
			"t0": text_node("点击"), "a": anchor("https://example.com/x"), "t1": text_node("查看，或"),
			"b": anchor("https://example.com/y"), "t2": text_node("。"),
		}}}
		self.assertEqual(render_rich_text(rich)["text"], "点击https://example.com/x 查看，或https://example.com/y。")

	def test_empty_paragraphs_collapse_to_one_blank_line(self):
		rich = {"elementIds": ["p1", "p2", "p3", "p4"], "elements": {"dictionary": {
			"p1": {"tag": 3, "property": b"", "childIds": ["c1"]}, "c1": text_node("第一段"),
			"p2": {"tag": 3, "property": b"", "childIds": ["c2"]}, "c2": text_node("\u00a0"),
			"p3": {"tag": 3, "property": b"", "childIds": ["c3"]}, "c3": text_node("\n"),
			"p4": {"tag": 3, "property": b"", "childIds": ["c4"]}, "c4": text_node("第二段"),
		}}}
		self.assertEqual(render_rich_text(rich)["text"], "第一段\n\n第二段")

	def test_mentions_emotions_and_images_use_the_upstream_placeholders(self):
		rich = {"elementIds": ["at", "t", "e", "i"], "elements": {"dictionary": {
			"at": {"tag": 5, "property": field(1, "7001") + field(2, "@张三")}, "t": text_node(" 看这里"),
			"e": {"tag": 10, "property": field(1, "SMILE")}, "i": {"tag": 2, "property": b"\x0a\x03abc"},
		}}}
		self.assertEqual(render_rich_text(rich)["text"], "@张三 看这里[表情][图片]")

	def test_malformed_input_never_raises_and_never_invents_text(self):
		self.assertEqual(render_rich_text(None), {"text": "", "links": []})
		self.assertEqual(render_rich_text({"elements": {"dictionary": {}}}), {"text": "", "links": []})
		cyclic = {"elementIds": ["a"], "elements": {"dictionary": {
			"a": {"tag": 3, "property": b"", "childIds": ["b", "missing"]}, "b": {"tag": 3, "property": b"\xff\xff\xff", "childIds": ["a"]},
		}}}
		self.assertEqual(render_rich_text(cyclic)["text"], "")
		# already-stringified (post json_safe) properties are not bytes: render nothing rather than garbage
		stringified = {"elementIds": ["t"], "elements": {"dictionary": {"t": {"tag": 1, "property": "\n\x05hello"}}}}
		self.assertEqual(render_rich_text(stringified)["text"], "")

	def test_undeclared_order_falls_back_to_numeric_ids(self):
		rich = {"elements": {"dictionary": {"2": text_node("二"), "10": text_node("十"), "1": text_node("一")}}}
		self.assertEqual(render_rich_text(rich)["text"], "一二十")

	def test_only_external_non_cdn_urls_are_relayable(self):
		self.assertTrue(is_relayable_url(BAIDU))
		self.assertFalse(is_relayable_url(STICKER))
		self.assertFalse(is_relayable_url("https://s1-imfile.feishucdn.com/static-resource/v1/x"))
		self.assertFalse(is_relayable_url("lark://applink"))


if __name__ == "__main__":
	unittest.main()
