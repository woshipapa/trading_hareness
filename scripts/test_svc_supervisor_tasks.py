"""Supervisor 任务清单的契约：停用的任务不能因为一次重启就自己回来。"""
import importlib
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import svc_supervisor


class TaskInventoryTests(unittest.TestCase):
    def _reload(self, **env):
        original = {k: os.environ.get(k) for k in env}
        os.environ.update({k: v for k, v in env.items() if v is not None})
        for key, value in env.items():
            if value is None:
                os.environ.pop(key, None)
        try:
            return importlib.reload(svc_supervisor)
        finally:
            for key, value in original.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

    def test_table_watch_stays_off_unless_explicitly_enabled(self):
        module = self._reload(ITOUGU_TABLE_WATCH=None)
        names = [t["name"] for t in module.TASKS]
        self.assertNotIn("itougu-table-watch", names)

    def test_table_watch_can_be_restored_with_one_flag(self):
        module = self._reload(ITOUGU_TABLE_WATCH="1")
        table_watch = [t for t in module.TASKS if t["name"] == "itougu-table-watch"]
        self.assertEqual(len(table_watch), 1)
        self.assertEqual(table_watch[0]["kind"], "daemon")

    def test_every_task_declares_a_name_and_kind(self):
        module = self._reload(ITOUGU_TABLE_WATCH=None)
        for task in module.TASKS:
            self.assertTrue(task.get("name"))
            self.assertIn(task.get("kind"), {"daemon", "interval", "daily", "calendar"})

    def test_task_names_are_unique(self):
        module = self._reload(ITOUGU_TABLE_WATCH="1")
        names = [t["name"] for t in module.TASKS]
        self.assertEqual(len(names), len(set(names)))


if __name__ == "__main__":
    unittest.main()
