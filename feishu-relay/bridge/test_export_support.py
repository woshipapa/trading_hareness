import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from export_support import NameCache, card_images_by_message, stored_card_images

KEY, IV = "ab" * 32, "cd" * 12


def card(images, *, content="[卡片] 研报摘要", content_data=None, msg_type="CARD"):
	return {"msg_type_name": msg_type, "content": content, "content_data": content_data or {}, "_larkagentx_images": images}


class NameCacheTests(unittest.TestCase):
	def test_names_are_looked_up_once_and_survive_a_restart(self):
		with tempfile.TemporaryDirectory() as directory:
			path = Path(directory) / "names.json"
			calls = []
			def lookup(user_id, chat_id):
				calls.append((user_id, chat_id))
				return {"7001": "小刘老师", "7002": " 自定义机器人 "}.get(user_id)
			cache = NameCache(path)
			self.assertEqual(cache.resolve("769", ["7001", "7002", "7001", "ou_x", "", None], lookup), {"7001": "小刘老师", "7002": "自定义机器人"})
			self.assertEqual(calls, [("7001", "769"), ("7002", "769")])
			self.assertEqual(cache.resolve("769", ["7001"], lookup), {"7001": "小刘老师"})
			self.assertEqual(len(calls), 2)
			self.assertEqual(oct(path.stat().st_mode & 0o777), "0o600")
			reopened = NameCache(path)
			self.assertEqual(reopened.resolve("769", ["7002"], lambda *_: self.fail("cached name must not hit the gateway")), {"7002": "自定义机器人"})

	def test_a_failed_or_empty_lookup_is_omitted_and_retried_later(self):
		with tempfile.TemporaryDirectory() as directory:
			cache = NameCache(Path(directory) / "names.json")
			def broken(user_id, _chat):
				if user_id == "7001":
					raise RuntimeError("gateway 400")
				return None
			self.assertEqual(cache.resolve("769", ["7001", "7002"], broken), {})
			self.assertEqual(len(cache), 0)
			self.assertEqual(cache.resolve("769", ["7001"], lambda *_: "后来查到了"), {"7001": "后来查到了"})

	def test_a_corrupt_cache_file_and_an_oversized_request_do_not_break_export(self):
		with tempfile.TemporaryDirectory() as directory:
			path = Path(directory) / "names.json"
			path.write_text("{not json", encoding="utf-8")
			self.assertEqual(NameCache(path).resolve("769", ["7001"], lambda *_: "甲"), {"7001": "甲"})
			with self.assertRaises(ValueError):
				NameCache(path).resolve("769", [str(n) for n in range(1000, 1400)], lambda *_: "x")


class CardImageTests(unittest.TestCase):
	def test_usable_card_images_are_returned_without_extra_fields(self):
		payload = card([{"image_id": "img_v3_a", "key_hex": KEY, "iv_hex": IV, "source_id": "5"}, {"image_id": "img_v3_a", "key_hex": KEY, "iv_hex": IV, "source_id": "9"}])
		self.assertEqual(stored_card_images(payload), [{"image_id": "img_v3_a", "key_hex": KEY, "iv_hex": IV}])

	def test_the_upgrade_banner_artwork_is_never_exported_as_content(self):
		banner = {"jsonCard": json.dumps({"body": {"elements": [{"tag": "plain_text", "property": {"content": "请升级至最新版本客户端，以查看内容"}}]}})}
		self.assertEqual(stored_card_images(card([{"image_id": "img_banner", "key_hex": KEY, "iv_hex": IV}], content_data=banner)), [])
		self.assertEqual(stored_card_images(card([{"image_id": "img_banner", "key_hex": KEY, "iv_hex": IV}], content="[卡片] 5Upgrade to the latest app version to view the content")), [])

	def test_banner_is_found_inside_nested_and_escaped_card_json(self):
		escaped = json.dumps({"newBody": {"x": [{"property": {"content": "请升级至最新版本客户端，以查看内容"}}]}})  # \\u-escaped inside the string
		self.assertNotIn("请升级", escaped)
		image = [{"image_id": "img_banner", "key_hex": KEY, "iv_hex": IV}]
		self.assertEqual(stored_card_images(card(image, content_data={"jsonCard": escaped})), [])
		self.assertEqual(stored_card_images(card(image, content="[卡片] " + escaped)), [])
		self.assertEqual(stored_card_images(card(image, content_data={"jsonCard": b'{"t": "Upgrade to the latest app version to view the content"}'})), [])
		# a real card whose text merely mentions upgrading is still content
		self.assertEqual(len(stored_card_images(card(image, content_data={"jsonCard": json.dumps({"t": "建议升级仓位"})}))), 1)

	def test_redacted_malformed_and_non_card_payloads_yield_nothing(self):
		self.assertEqual(stored_card_images(card([{"image_id": "img", "key_hex": "[redacted]", "iv_hex": "[redacted]"}])), [])
		self.assertEqual(stored_card_images(card([{"image_id": "", "key_hex": KEY, "iv_hex": IV}, "junk", {"image_id": "img", "key_hex": KEY}])), [])
		self.assertEqual(stored_card_images(card([{"image_id": "img", "key_hex": KEY, "iv_hex": IV}], msg_type="POST")), [])
		self.assertEqual(stored_card_images(None), [])

	def test_lookup_is_by_chat_and_message_id_against_the_spool(self):
		spool = {"larkagentx:769:111": card([{"image_id": "img_a", "key_hex": KEY, "iv_hex": IV}]), "larkagentx:769:222": card([])}
		asked = []
		def load(event_id):
			asked.append(event_id)
			return spool.get(event_id)
		requests = [{"chat_id": "769", "message_id": "111"}, {"chat_id": "769", "message_id": "222"}, {"chat_id": "769", "message_id": "333"},
		            {"chat_id": "oc_x", "message_id": "111"}, {"chat_id": "769", "message_id": "om_x"}, "junk", {"chat_id": "769", "message_id": "111"}]
		self.assertEqual(card_images_by_message(requests, load), {"111": [{"image_id": "img_a", "key_hex": KEY, "iv_hex": IV}]})
		self.assertEqual(asked, ["larkagentx:769:111", "larkagentx:769:222", "larkagentx:769:333"])
		with self.assertRaises(ValueError):
			card_images_by_message({"chat_id": "769"}, load)


if __name__ == "__main__":
	unittest.main()
