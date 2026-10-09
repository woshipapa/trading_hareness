"""owner_collab_watch：只报对方的新提交，强推改写要告警，发送失败不推进状态。"""
import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent))
import owner_collab_watch as watch  # noqa: E402

WEBHOOK = watch.WEBHOOK_PREFIX + "test-token"


class Repo:
    def __init__(self, base: Path):
        self.base = base
        self.remote, self.work = base / "origin.git", base / "work"
        self.git("init", "--quiet", "--bare", "-b", "main", str(self.remote), cwd=base)
        self.git("clone", "--quiet", str(self.remote), str(self.work), cwd=base)
        self.git("checkout", "--quiet", "-b", "main")
        self.commit("base", "us")
        self.git("push", "--quiet", "origin", "main", f"main:refs/heads/{watch.BRANCH}")

    def git(self, *args, cwd=None, author="us"):
        env = {**os.environ, "GIT_AUTHOR_NAME": author, "GIT_AUTHOR_EMAIL": f"{author}@example.com",
               "GIT_COMMITTER_NAME": author, "GIT_COMMITTER_EMAIL": f"{author}@example.com"}
        return subprocess.run(["git", *args], cwd=cwd or self.work, capture_output=True, text=True,
                              check=True, env=env).stdout

    def commit(self, subject, author, *, start=None, force=False):
        if start:
            self.git("checkout", "--quiet", "-B", "scratch", start)
        (self.work / f"{subject.replace(' ', '_')}.txt").write_text(subject, encoding="utf-8")
        self.git("add", "-A")
        self.git("commit", "--quiet", "-m", subject, author=author)
        if start:
            self.git("push", "--quiet", *(["--force"] if force else []), "origin", f"HEAD:refs/heads/{watch.BRANCH}")

    def push_branch(self, subject, author, *, force=False, start=None):
        self.git("fetch", "--quiet", "origin")
        self.commit(subject, author, start=start or f"origin/{watch.BRANCH}", force=force)


class WatchTests(unittest.TestCase):
    def setUp(self):
        self.base = Path(tempfile.mkdtemp(prefix="owner-watch-test-"))
        self.addCleanup(shutil.rmtree, self.base, ignore_errors=True)
        self.repo = Repo(self.base)
        self.state = self.base / "state.json"
        for patcher in (mock.patch.object(watch, "STATE_PATH", self.state),
                        mock.patch.dict(os.environ, {watch.WEBHOOK_ENV: WEBHOOK})):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.sent = []

    def sender(self, text, url, key):
        self.sent.append(text)
        return {"status": "sent"}

    def tick(self, **kwargs):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = watch.run(root=self.repo.work, our_authors=["us"], sender=kwargs.pop("sender", self.sender), **kwargs)
        return code, out.getvalue()

    def test_the_first_run_only_records_a_baseline(self):
        self.assertEqual(self.tick()[0], 0)
        self.assertEqual(self.sent, [])
        self.assertEqual(json.loads(self.state.read_text())["last_event"], "baseline")

    def test_their_commits_are_reported_once_and_ours_never(self):
        self.tick()
        self.repo.push_branch("our sync", "us")
        self.assertEqual(self.tick()[0], 0)
        self.assertEqual(self.sent, [], "our own commits are not news")
        self.repo.push_branch("fix close gate", "owner-windows")
        self.assertEqual(self.tick()[0], 0)
        self.assertEqual(len(self.sent), 1)
        self.assertIn("fix close gate（owner-windows）", self.sent[0])
        self.assertNotIn("@example.com", self.sent[0])
        self.tick()
        self.assertEqual(len(self.sent), 1, "nothing new on the next tick")

    def test_a_force_push_raises_an_alarm(self):
        self.repo.git("fetch", "--quiet", "origin")
        base = self.repo.git("rev-parse", f"origin/{watch.BRANCH}").strip()
        self.repo.push_branch("their work", "owner-windows")
        self.tick()
        self.repo.push_branch("rewritten", "owner-windows", force=True, start=base)
        self.assertEqual(self.tick()[0], 0)
        self.assertIn("历史被改写", self.sent[-1])
        self.assertEqual(json.loads(self.state.read_text())["last_event"], "rewrite-alarm")

    def test_a_failed_send_keeps_the_state_for_the_next_tick(self):
        self.tick()
        before = json.loads(self.state.read_text())["last_seen_sha"]
        self.repo.push_branch("their work", "owner-windows")
        code, _ = self.tick(sender=lambda *_: {"status": "failed"})
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(self.state.read_text())["last_seen_sha"], before)
        self.assertEqual(self.tick()[0], 0)
        self.assertEqual(len(self.sent), 1)

    def test_without_a_webhook_the_message_is_logged_and_the_state_moves(self):
        self.tick()
        self.repo.push_branch("their work", "owner-windows")
        with mock.patch.dict(os.environ, {watch.WEBHOOK_ENV: ""}):
            code, out = self.tick()
        self.assertEqual(code, 0)
        self.assertIn("message logged only", out)
        self.assertNotIn(WEBHOOK, out)
        self.assertEqual(json.loads(self.state.read_text())["last_event"], "sent-logged")


if __name__ == "__main__":
    unittest.main()
