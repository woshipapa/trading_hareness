import os
import unittest
from unittest.mock import patch

from entrypoint import migrations_enabled


class EntrypointTests(unittest.TestCase):
    def test_peer_can_explicitly_skip_migrations(self):
        with patch.dict(os.environ, {"QUANT_SKIP_MIGRATIONS": "true"}, clear=False):
            self.assertFalse(migrations_enabled())

    def test_migrations_remain_enabled_by_default(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertTrue(migrations_enabled())


if __name__ == "__main__":
    unittest.main()
