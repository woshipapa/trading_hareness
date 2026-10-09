"""b300_collab_watch：只报执行方的新提交，失败不推进状态，消息不带地址与邮箱。"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent))
import b300_collab_watch as watch  # noqa: E402

HOOK = watch.WEBHOOK_PREFIX + "fixture-token-not-real"


def commit(sha, subject, author="b300-exec-agent"):
    return {"sha": sha * 40, "author": author, "subject": subject}


class PureFunctionTests(unittest.TestCase):
    def test_new_commits_stop_at_the_last_seen_sha(self):
        commits = [commit("c", "status: c"), commit("b", "run: b"), commit("a", "run: a")]
        fresh, found = watch.new_commits(commits, "b" * 40)
        self.assertEqual([c["sha"][0] for c in fresh], ["c"])
        self.assertTrue(found)

    def test_a_missing_last_seen_sha_is_reported_as_a_gap(self):
        commits = [commit("c", "status: c"), commit("b", "run: b")]
        fresh, found = watch.new_commits(commits, "z" * 40)
        self.assertEqual(len(fresh), 2)
        self.assertFalse(found)
        text = watch.build_message(fresh, history_gap=not found)
        self.assertIn("不在最近", text)

    def test_message_lists_attention_items_and_scrubs_email(self):
        executor = [commit("b", "run: 门禁 static_2sm FAIL（复现 3/3）"),
                    commit("a", "status: 联系 someone@example.com 后 PASS")]
        text = watch.build_message(executor, history_gap=False)
        self.assertTrue(text.startswith("B300 协作：执行方推送了 2 个提交（最新 bbbbbbb）"))
        self.assertIn("需要审查方处理：\n- bbbbbbb run: 门禁 static_2sm FAIL", text)
        self.assertNotIn("example.com", text)
        self.assertTrue(text.endswith(watch.BRANCH_URL))
        # oldest first in the list
        self.assertLess(text.index("aaaaaaa"), text.index("bbbbbbb run"))

    def test_zero_failure_counts_are_not_attention_but_failed_selections_are(self):
        attn = watch.needs_attention
        self.assertFalse(attn("run: 锁频选档 1170 短 campaign（GPU4，audit pass，check 0 FAIL）"))
        self.assertFalse(attn("run: 门禁 static_2sm 重跑 PASS（0 失败）"))
        self.assertTrue(attn("run: 锁频选档格文件与 decide 结果（chosen=null，三档均未过，阳性对照有效）"))
        self.assertTrue(attn("run: 门禁 static_2sm FAIL（复现 3/3）"))
        self.assertTrue(attn("run: check 10 FAIL"))
        self.assertTrue(attn("run: 2 个失败"))

    def test_message_stays_within_the_feishu_limit(self):
        executor = [commit(chr(97 + i % 26), "run: " + "长" * 300) for i in range(60)]
        text = watch.build_message(executor, history_gap=False)
        self.assertLessEqual(len(text), watch.LIMIT)
        self.assertIn("另有", text)

    def test_only_feishu_bot_urls_count_as_configured(self):
        with mock.patch.dict(os.environ, {watch.WEBHOOK_ENV: HOOK}):
            self.assertEqual(watch.webhook_url(), HOOK)
        for bad in ("", "http://open.feishu.cn/open-apis/bot/v2/hook/x", watch.WEBHOOK_PREFIX,
                    "https://example.com/open-apis/bot/v2/hook/x"):
            with mock.patch.dict(os.environ, {watch.WEBHOOK_ENV: bad}):
                self.assertEqual(watch.webhook_url(), "")

    def test_result_logging_never_contains_the_webhook(self):
        text = watch.safe_result({"status": "failed", "error": f"boom {HOOK}", "body": HOOK}, HOOK)
        self.assertNotIn("fixture-token-not-real", text)
        self.assertNotIn("body", text)


class RunTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.old_state = watch.STATE_PATH
        watch.STATE_PATH = Path(self.dir.name, "state.json")

    def tearDown(self):
        watch.STATE_PATH = self.old_state
        self.dir.cleanup()

    def state(self):
        return json.loads(watch.STATE_PATH.read_text())["last_seen_sha"][0]

    def test_first_run_records_a_baseline_and_sends_nothing(self):
        sender = mock.Mock()
        self.assertEqual(watch.run(fetch=lambda: [commit("a", "run: a")], sender=sender), 0)
        self.assertEqual(self.state(), "a")
        sender.assert_not_called()

    def test_failed_send_keeps_the_state_and_a_later_success_advances_it(self):
        watch.run(fetch=lambda: [commit("a", "run: a")])
        commits = [commit("b", "run: 门禁 FAIL"), commit("a", "run: a")]
        failing = mock.Mock(return_value={"status": "failed", "http": 500})
        with mock.patch.dict(os.environ, {watch.WEBHOOK_ENV: HOOK}):
            self.assertEqual(watch.run(fetch=lambda: commits, sender=failing), 1)
            self.assertEqual(self.state(), "a")
            ok = mock.Mock(return_value={"status": "sent"})
            self.assertEqual(watch.run(fetch=lambda: commits, sender=ok), 0)
        self.assertEqual(self.state(), "b")
        text, url, key = ok.call_args.args
        self.assertEqual(url, HOOK)
        self.assertEqual(key, "b300-collab-" + "b" * 40)
        self.assertIn("需要审查方处理", text)

    def test_reviewer_only_commits_advance_quietly(self):
        watch.run(fetch=lambda: [commit("a", "run: a")])
        sender = mock.Mock()
        with mock.patch.dict(os.environ, {watch.WEBHOOK_ENV: HOOK}):
            watch.run(fetch=lambda: [commit("b", "review: tenth", author="yp"), commit("a", "run: a")],
                      sender=sender)
        sender.assert_not_called()
        self.assertEqual(self.state(), "b")

    def test_without_a_webhook_the_message_is_only_logged(self):
        watch.run(fetch=lambda: [commit("a", "run: a")])
        sender = mock.Mock()
        with mock.patch.dict(os.environ, {watch.WEBHOOK_ENV: ""}):
            self.assertEqual(watch.run(fetch=lambda: [commit("b", "run: b"), commit("a", "run: a")],
                                       sender=sender), 0)
        sender.assert_not_called()
        self.assertEqual(self.state(), "b")

    def test_dry_run_never_writes_state_or_sends(self):
        sender = mock.Mock()
        with mock.patch.dict(os.environ, {watch.WEBHOOK_ENV: HOOK}):
            self.assertEqual(watch.run(dry_run=True, fetch=lambda: [commit("a", "run: a")], sender=sender), 0)
        self.assertFalse(watch.STATE_PATH.exists())
        sender.assert_not_called()


class SupervisorEntryTests(unittest.TestCase):
    """supervisor 每 5 分钟跑一次；webhook 只在配置了时才交给子进程。"""

    def _task(self, **env):
        import importlib
        import svc_supervisor
        with mock.patch.dict(os.environ, env):
            for key, value in env.items():
                if value is None:
                    os.environ.pop(key, None)
            module = importlib.reload(svc_supervisor)
        task = next(t for t in module.TASKS if t["name"] == "b300-collab.watch")
        importlib.reload(svc_supervisor)
        return task

    def test_interval_task_runs_the_watch_script(self):
        task = self._task(B300_COLLAB_FEISHU_WEBHOOK_URL=HOOK)
        self.assertEqual((task["kind"], task["interval"], task["run_at_load"]), ("interval", 300, True))
        self.assertTrue(task["args"][1].endswith("scripts/b300_collab_watch.py"))
        self.assertTrue(task["out"].endswith("logs/b300-collab-watch.log"))
        self.assertEqual(task["env"]["B300_COLLAB_FEISHU_WEBHOOK_URL"], HOOK)
        self.assertIn("/opt/homebrew/bin", task["env"]["PATH"])  # gh lives there

    def test_an_unset_webhook_is_not_passed_to_the_child(self):
        import svc_supervisor
        with mock.patch.object(svc_supervisor, "CENTRAL_SECRETS_ENV", "/nonexistent"), \
                mock.patch.object(svc_supervisor, "LEGACY_LOCAL_ENV", "/nonexistent"), \
                mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("B300_COLLAB_FEISHU_WEBHOOK_URL", None)
            self.assertEqual(svc_supervisor._load_env_secret("B300_COLLAB_FEISHU_WEBHOOK_URL"), "")


if __name__ == "__main__":
    unittest.main()
