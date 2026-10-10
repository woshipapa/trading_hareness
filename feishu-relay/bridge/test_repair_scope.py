import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from repair_scope import repair_allowed


class RepairScopeTests(unittest.TestCase):
	def test_unsubscribed_or_non_numeric_chat_is_never_repaired(self):
		self.assertFalse(repair_allowed("1", websocket_chat_ids={"2"}, configured_chat_ids=set(), dynamic_chat_ids=set()))
		self.assertFalse(repair_allowed("oc_x", websocket_chat_ids={"oc_x"}, configured_chat_ids=set(), dynamic_chat_ids=set()))

	def test_empty_configured_list_repairs_every_subscribed_chat(self):
		self.assertTrue(repair_allowed("7", websocket_chat_ids={"7"}, configured_chat_ids=set(), dynamic_chat_ids=set()))

	def test_configured_list_still_limits_static_chats(self):
		self.assertTrue(repair_allowed("7", websocket_chat_ids={"7", "8"}, configured_chat_ids={"7"}, dynamic_chat_ids=set()))
		self.assertFalse(repair_allowed("8", websocket_chat_ids={"7", "8"}, configured_chat_ids={"7"}, dynamic_chat_ids=set()))

	def test_dynamically_bound_chat_is_repaired_without_env_edit(self):
		# A group added from the dashboard by name must get gap repair without
		# a restart-time env change, so dynamic bindings count as configured.
		self.assertTrue(repair_allowed("9", websocket_chat_ids={"7", "9"}, configured_chat_ids={"7"}, dynamic_chat_ids={"9"}))


if __name__ == "__main__":
	unittest.main()
