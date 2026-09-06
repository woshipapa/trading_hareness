import unittest

from app.longhu_schema_profile import profile


class LonghuSchemaProfileTests(unittest.TestCase):
    def test_profile_exposes_paths_and_types_but_never_provider_values(self):
        result = profile([{
            "target": "longhu_history", "action": "MorningBiddingList",
            "payload": {"list": [{"StockID": "600000", "amount": 123.45}]},
        }])
        self.assertEqual(result["status"], "observed")
        contract = result["contracts"][0]
        self.assertFalse(contract["factor_eligible"])
        self.assertEqual(contract["schema_review_status"], "unreviewed")
        fields = {item["path"]: item for item in contract["fields"]}
        self.assertEqual(fields["list[].StockID"]["types"], ["string"])
        self.assertNotIn("600000", str(result))
        self.assertNotIn("123.45", str(result))
