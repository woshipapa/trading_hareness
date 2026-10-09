"""collab_branch：协作分支只在全部检查通过后才合并进 main，合并后两边对齐，改写历史会被发现。"""
import contextlib
import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent))
import collab_branch as cb  # noqa: E402

IDENTITY = {"GIT_AUTHOR_NAME": "tester", "GIT_AUTHOR_EMAIL": "tester@example.com",
            "GIT_COMMITTER_NAME": "tester", "GIT_COMMITTER_EMAIL": "tester@example.com"}
FAST = ["--skip-tests", "--skip-repo-checks"]


def migration(revision, down):
    down_text = repr(down)
    return f'"""m"""\nrevision = "{revision}"\ndown_revision = {down_text}\n'


class PureCheckTests(unittest.TestCase):
    def test_revision_reads_annotated_and_tuple_parents(self):
        source = 'revision: str = "m1"\ndown_revision: tuple[str, str] = ("a", "b")\n'
        self.assertEqual(cb.parse_revision(source), ("m1", ("a", "b")))
        self.assertEqual(cb.parse_revision('revision = "r"\ndown_revision = None\n'), ("r", ()))

    def test_a_clean_chain_has_no_problems(self):
        sources = {"a.py": migration("a", None), "b.py": migration("b", "a"), "c.py": migration("c", "b")}
        self.assertEqual(cb.migration_problems(sources), [])

    def test_duplicates_missing_parents_two_heads_and_cycles_are_reported(self):
        self.assertIn("duplicate revision a", " ".join(cb.migration_problems(
            {"a.py": migration("a", None), "a2.py": migration("a", None)})))
        self.assertIn("down_revision z is not in the chain", " ".join(cb.migration_problems(
            {"a.py": migration("a", None), "b.py": migration("b", "z")})))
        self.assertIn("2 heads", " ".join(cb.migration_problems(
            {"a.py": migration("a", None), "b.py": migration("b", "a"), "c.py": migration("c", "a")})))
        problems = " ".join(cb.migration_problems({"a.py": migration("a", "b"), "b.py": migration("b", "a")}))
        self.assertIn("cycle", problems)

    def test_an_applied_migration_may_not_change_or_vanish(self):
        before = {"a.py": "x", "b.py": "y"}
        self.assertEqual(cb.edited_migrations(before, {"a.py": "x", "b.py": "y", "c.py": "z"}), [])
        self.assertEqual(cb.edited_migrations(before, {"a.py": "x2"}), ["a.py", "b.py"])

    def test_env_and_key_files_are_refused_but_examples_pass(self):
        paths = [".env", "deploy/.env.local", "config/owner-secrets.env", "keys/id_ed25519", "x/server.pem",
                 ".env.example", "config/secrets/env.example", "quant-service/app/settings.py"]
        self.assertEqual(cb.forbidden_paths(paths), [".env", "config/owner-secrets.env", "deploy/.env.local",
                                                     "keys/id_ed25519", "x/server.pem"])


class Sandbox:
    """A bare 'origin' with main and collab/owner-peer, and a clone the tool runs in."""

    def __init__(self):
        self.base = Path(tempfile.mkdtemp(prefix="collab-test-"))
        self.remote = self.base / "origin.git"
        self.work = self.base / "work"
        self.git("init", "--quiet", "--bare", "-b", "main", str(self.remote), cwd=self.base)
        self.git("clone", "--quiet", str(self.remote), str(self.work), cwd=self.base)
        self.write({f"{cb.MIGRATIONS}/a.py": migration("a", None), f"{cb.MIGRATIONS}/b.py": migration("b", "a"),
                    "README.md": "base\n"})
        self.git("checkout", "--quiet", "-b", "main")
        self.commit("base")
        self.git("push", "--quiet", "origin", "main")
        self.git("push", "--quiet", "origin", f"main:refs/heads/{cb.COLLAB}")

    def git(self, *args, cwd=None):
        return subprocess.run(["git", *args], cwd=cwd or self.work, capture_output=True, text=True, check=True).stdout

    def write(self, files):
        for name, text in files.items():
            path = self.work / name
            if text is None:
                path.unlink()
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")

    def commit(self, subject):
        self.git("add", "-A")
        self.git("commit", "--quiet", "-m", subject)

    def on(self, branch, files, subject, *, force=False, start=None):
        self.git("fetch", "--quiet", "origin")
        self.git("checkout", "--quiet", "-B", "scratch", start or f"origin/{branch}")
        self.write(files)
        self.commit(subject)
        self.git("push", "--quiet", *(["--force"] if force else []), "origin", f"HEAD:refs/heads/{branch}")

    def tip(self, branch):
        self.git("fetch", "--quiet", "origin")
        return self.git("rev-parse", f"origin/{branch}").strip()

    def run(self, *argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = cb.main([*argv], root=self.work)
        return code, out.getvalue()


class BranchFlowTests(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.dict(os.environ, IDENTITY)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.box = Sandbox()
        self.addCleanup(shutil.rmtree, self.box.base, ignore_errors=True)

    def test_a_clean_branch_merges_into_main_and_the_branch_follows(self):
        self.box.on(cb.COLLAB, {f"{cb.MIGRATIONS}/c.py": migration("c", "b"), "peer.txt": "fix\n"}, "peer fix")
        code, out = self.box.run("merge", *FAST)
        self.assertEqual(code, 0, out)
        self.assertEqual(self.box.tip("main"), self.box.tip(cb.COLLAB))
        subject = self.box.git("log", "-1", "--format=%s", "origin/main")
        self.assertTrue(subject.startswith(cb.MERGE_PREFIX), subject)
        self.assertIn("peer.txt", self.box.git("ls-tree", "-r", "--name-only", "origin/main"))

    def test_check_never_writes(self):
        self.box.on(cb.COLLAB, {"peer.txt": "fix\n"}, "peer fix")
        before = (self.box.tip("main"), self.box.tip(cb.COLLAB))
        code, out = self.box.run("check", *FAST)
        self.assertEqual(code, 0, out)
        self.assertEqual((self.box.tip("main"), self.box.tip(cb.COLLAB)), before)

    def test_an_edited_applied_migration_blocks_the_merge(self):
        self.box.on(cb.COLLAB, {f"{cb.MIGRATIONS}/a.py": migration("a", None) + "# changed\n"}, "edit a")
        before = self.box.tip("main")
        code, out = self.box.run("merge", *FAST)
        self.assertEqual(code, 1, out)
        self.assertIn("[FAIL] migrations on main left untouched: a.py", out)
        self.assertEqual(self.box.tip("main"), before)

    def test_an_env_file_blocks_the_merge(self):
        self.box.on(cb.COLLAB, {"deploy/.env": "KEY=1\n"}, "oops")
        code, out = self.box.run("check", *FAST)
        self.assertEqual(code, 1, out)
        self.assertIn("[FAIL] no secret or env files: deploy/.env", out)

    def test_two_migration_heads_after_the_merge_block_it(self):
        self.box.on("main", {f"{cb.MIGRATIONS}/d.py": migration("d", "b")}, "ours")
        self.box.on(cb.COLLAB, {f"{cb.MIGRATIONS}/c.py": migration("c", "b")}, "theirs")
        code, out = self.box.run("check", *FAST)
        self.assertEqual(code, 1, out)
        self.assertIn("2 heads (c, d)", out)

    def test_a_force_push_over_merged_history_is_caught(self):
        base = self.box.tip(cb.COLLAB)
        self.box.on(cb.COLLAB, {"peer.txt": "fix\n"}, "peer fix")
        self.assertEqual(self.box.run("merge", *FAST)[0], 0)
        self.box.on(cb.COLLAB, {"other.txt": "x\n"}, "rewritten", force=True, start=base)
        code, out = self.box.run("check", *FAST)
        self.assertEqual(code, 1, out)
        self.assertIn("[FAIL] history not rewritten", out)

    def test_a_regenerated_file_matching_the_staged_merge_is_not_stale(self):
        # A staged merge lists the branch's index change as modified; that alone is not staleness.
        self.box.write({"docs/ARCHITECTURE_INDEX.md": "branch version\n"})
        self.box.git("add", "docs/ARCHITECTURE_INDEX.md")
        self.assertFalse(cb.regenerated_differs(self.box.work, "docs/ARCHITECTURE_INDEX.md"))
        self.box.write({"docs/ARCHITECTURE_INDEX.md": "what the generator would write\n"})
        self.assertTrue(cb.regenerated_differs(self.box.work, "docs/ARCHITECTURE_INDEX.md"))

    def test_sync_merges_main_into_the_branch_without_rewriting_it(self):
        self.box.on(cb.COLLAB, {"peer.txt": "fix\n"}, "peer fix")
        peer_tip = self.box.tip(cb.COLLAB)
        self.box.on("main", {"ours.txt": "x\n"}, "ours")
        code, out = self.box.run("sync", "--skip-tests")
        self.assertEqual(code, 0, out)
        collab = self.box.tip(cb.COLLAB)
        self.box.git("merge-base", "--is-ancestor", peer_tip, collab)
        self.box.git("merge-base", "--is-ancestor", self.box.tip("main"), collab)
        self.assertTrue(self.box.git("log", "-1", "--format=%s", collab).startswith(cb.SYNC_PREFIX))
        self.assertEqual(self.box.run("check", *FAST)[0], 0)

    def test_sync_fast_forwards_when_the_branch_has_nothing_of_its_own(self):
        self.box.on("main", {"ours.txt": "x\n"}, "ours")
        code, out = self.box.run("sync", "--skip-tests")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.box.tip(cb.COLLAB), self.box.tip("main"))


if __name__ == "__main__":
    unittest.main()
