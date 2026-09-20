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

    def test_paper_source_snapshot_task_is_bounded_and_non_mutating(self):
        module = self._reload(ITOUGU_TABLE_WATCH=None, HF_TOKEN=None)
        task = next(item for item in module.TASKS if item["name"] == "paperkb.sources")
        self.assertEqual(task["kind"], "daily")
        self.assertIn("datacite", task["args"])
        repository = next(item for item in module.TASKS if item["name"] == "paperkb.repository-sources")
        self.assertEqual(repository["kind"], "daily")
        self.assertIn("europe_pmc", repository["args"])
        self.assertIn("zenodo", repository["args"])
        digest = next(item for item in module.TASKS if item["name"] == "paperkb.supplemental-digest")
        self.assertEqual(digest["kind"], "daily")
        self.assertIn("supplemental_digest.py", digest["args"][1])
        self.assertIn("--notify", digest["args"])

    def test_local_scholar_alerts_are_opt_in(self):
        module = self._reload(ITOUGU_TABLE_WATCH=None, HF_TOKEN=None, PAPER_KB_SCHOLAR_ALERT_DIR=None)
        self.assertNotIn("paperkb.scholar-alerts", [t["name"] for t in module.TASKS])
        module = self._reload(ITOUGU_TABLE_WATCH=None, HF_TOKEN=None,
                              PAPER_KB_SCHOLAR_ALERT_DIR="/tmp/scholar-alerts")
        task = next(item for item in module.TASKS if item["name"] == "paperkb.scholar-alerts")
        self.assertIn("/tmp/scholar-alerts", task["args"])

    def test_s2_children_receive_optional_provider_credentials_at_runtime(self):
        module = self._reload(ITOUGU_TABLE_WATCH=None, HF_TOKEN=None,
                              OPENALEX_API_KEY="openalex-test-only",
                              OPENCITATIONS_ACCESS_TOKEN="opencitations-test-only",
                              PAPER_KB_OPENALEX_ENABLED="true",
                              PAPER_KB_OPENCITATIONS_ENABLED="false")
        for name in ("paperkb.s2", "paperkb.s2-recovery", "paperkb.citation-watch"):
            task = next(item for item in module.TASKS if item["name"] == name)
            self.assertEqual(task["env"]["OPENALEX_API_KEY"], "openalex-test-only")
            self.assertEqual(task["env"]["OPENCITATIONS_ACCESS_TOKEN"], "opencitations-test-only")
            self.assertEqual(task["env"]["PAPER_KB_OPENALEX_ENABLED"], "true")
            self.assertEqual(task["env"]["PAPER_KB_OPENCITATIONS_ENABLED"], "false")


if __name__ == "__main__":
    unittest.main()
