"""Pure tests for immutable research manifest identity."""

from datetime import date, datetime, timezone
import unittest

from app.research_manifest import manifest_digest, snapshot_key


class ResearchManifestTests(unittest.TestCase):
    def test_digest_is_order_independent_for_mapping_keys(self):
        self.assertEqual(
            manifest_digest({"bars": 10, "reports": 2}),
            manifest_digest({"reports": 2, "bars": 10}),
        )

    def test_digest_changes_when_evidence_changes(self):
        self.assertNotEqual(
            manifest_digest({"bars": 10}),
            manifest_digest({"bars": 11}),
        )

    def test_snapshot_key_contains_the_cutoff_and_manifest_digest(self):
        cutoff = datetime(2026, 8, 27, 8, tzinfo=timezone.utc)
        digest = manifest_digest({"bars": 10})

        first = snapshot_key(date(2026, 8, 27), cutoff, digest)
        second = snapshot_key(date(2026, 8, 27), cutoff.replace(hour=9), digest)

        self.assertEqual(len(first), 64)
        self.assertNotEqual(first, second)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
