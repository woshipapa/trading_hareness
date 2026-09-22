import tempfile
import unittest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from owner_lock import OwnerLock
from owner_lock import profile_storage_paths


class OwnerLockTests(unittest.TestCase):
    def test_lock_is_exclusive_until_release(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "owner.lock"
            first = OwnerLock(path)
            second = OwnerLock(path)
            first.acquire()
            with self.assertRaises(RuntimeError):
                second.acquire()
            first.release()
            second.acquire()
            self.assertTrue(second.held)
            second.release()

    def test_non_default_profile_isolated_from_default_credentials_and_spool(self):
        credentials, spool, owner = profile_storage_paths("~/larkx-test", "research-1")
        self.assertEqual(str(credentials), "~/larkx-test/profiles/research-1/credentials.json".replace("~", str(Path.home())))
        self.assertEqual(spool.parent, credentials.parent)
        self.assertEqual(owner.parent, credentials.parent)

    def test_profile_name_cannot_escape_storage_root(self):
        with self.assertRaises(ValueError):
            profile_storage_paths("/tmp/larkx-test", "../escape")


if __name__ == "__main__":
    unittest.main()
