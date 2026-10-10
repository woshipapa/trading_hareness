import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from group_lookup import GroupLookupError, strip_highlight, unique_group_id


class GroupLookupTests(unittest.TestCase):
	def test_highlight_markup_is_stripped(self):
		self.assertEqual(strip_highlight("<h>小杰夜报B</h>"), "小杰夜报B")
		self.assertEqual(strip_highlight("小杰<h>夜报</h>B"), "小杰夜报B")

	def test_exact_unique_group_is_selected(self):
		results = [
			{"type": "group", "id": "7694956403840011216", "title": "<h>小杰夜报B</h>"},
			{"type": "group", "id": "7681111111111111111", "title": "<h>小杰夜报</h>0818"},
			{"type": "user", "id": "123", "title": "<h>小杰夜报B</h>"},
		]
		self.assertEqual(unique_group_id(results, "小杰夜报B"), "7694956403840011216")

	def test_substring_match_is_never_enough(self):
		with self.assertRaises(GroupLookupError):
			unique_group_id([{"type": "group", "id": "1", "title": "<h>小杰夜报</h>0818"}], "小杰夜报")

	def test_duplicate_names_fail_closed(self):
		results = [{"type": "group", "id": "1", "title": "同名"}, {"type": "group", "id": "2", "title": "同名"}]
		with self.assertRaises(GroupLookupError) as ctx:
			unique_group_id(results, "同名")
		self.assertIn("同名群", str(ctx.exception))

	def test_same_group_listed_twice_is_not_ambiguous(self):
		results = [{"type": "group", "id": "5", "title": "A"}, {"type": "group", "id": "5", "title": "<h>A</h>"}]
		self.assertEqual(unique_group_id(results, "A"), "5")

	def test_missing_or_non_numeric_ids_are_rejected(self):
		with self.assertRaises(GroupLookupError):
			unique_group_id([], "A")
		with self.assertRaises(GroupLookupError):
			unique_group_id([{"type": "group", "id": "oc_x", "title": "A"}], "A")
		with self.assertRaises(GroupLookupError):
			unique_group_id([{"type": "group", "id": "5", "title": "A"}], "  ")


if __name__ == "__main__":
	unittest.main()
