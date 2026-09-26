import unittest

from source_filter import is_anqiang_source, matched_source_keyword, normalize_filter_text


class SourceFilterTests(unittest.TestCase):
    def test_normalizes_schema_2_card_text_and_punctuation(self):
        message = {
            "msg_type_name": "CARD",
            "content_data": {
                "jsonCard": '{"body":{"elements":[{"tag":"markdown","content":"般若星　登山的川柏！"}]}}'
            },
        }
        self.assertEqual(
            matched_source_keyword(
                message,
                source_key="anqiang",
                chat_name="安强训练营1（50）",
                configured_source_keys={"anqiang"},
                keywords={"般若星登山的川柏"},
            ),
            "般若星登山的川柏",
        )

    def test_source_filter_does_not_apply_to_unrelated_route(self):
        message = {"content": "般若星登山的川柏"}
        self.assertIsNone(
            matched_source_keyword(
                message,
                source_key="liwei",
                chat_name="立伟08，严禁偷盗群信息",
                configured_source_keys={"anqiang"},
                keywords={"般若星登山的川柏"},
            )
        )

    def test_anqiang_name_identifies_persisted_vip_route(self):
        self.assertTrue(is_anqiang_source("relay_vip", "马安强VIP高端训练营", {"anqiang"}))
        self.assertTrue(is_anqiang_source("", "", {"anqiang"}, chat_id="7661209668907207659", configured_chat_ids={"7661209668907207659"}))
        self.assertFalse(is_anqiang_source("liwei", "立伟群", {"anqiang"}))
        self.assertEqual(normalize_filter_text("般若星-登山的川柏"), "般若星登山的川柏")


if __name__ == "__main__":
    unittest.main()
