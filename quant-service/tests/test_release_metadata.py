from __future__ import annotations

import unittest

from app.release_metadata import release_metadata


class ReleaseMetadataTests(unittest.TestCase):
    def test_reports_only_safe_build_provenance(self) -> None:
        payload = release_metadata({
            "APP_GIT_SHA": "A1B2C3D4",
            "APP_RELEASE": "edge-2026.08.24.1",
            "APP_BUILD_CREATED_AT": "2026-08-24T12:00:00Z",
        })

        self.assertEqual(payload["git_sha"], "a1b2c3d4")
        self.assertEqual(payload["release"], "edge-2026.08.24.1")
        self.assertEqual(payload["build_created_at"], "2026-08-24T12:00:00Z")

    def test_a_hot_release_names_the_code_actually_running(self) -> None:
        payload = release_metadata(
            {"APP_GIT_SHA": "d5c359dee3b6", "APP_RELEASE": "20260921T154500Z-teacher-review-d5c359d"},
            module_file="/app/hotfix/releases/hotfix-20260922T150930Z-265d551f7b/app/release_metadata.py",
        )
        self.assertEqual(payload["release"], "20260921T154500Z-teacher-review-d5c359d")   # the image underneath
        self.assertEqual(payload["hotfix_release"], "hotfix-20260922T150930Z-265d551f7b")
        self.assertEqual(payload["running_git_sha"], "265d551f7b")

    def test_without_a_hot_release_the_image_is_what_runs(self) -> None:
        payload = release_metadata({"APP_GIT_SHA": "d5c359de"}, module_file="/app/app/release_metadata.py")
        self.assertIsNone(payload["hotfix_release"])
        self.assertEqual(payload["running_git_sha"], "d5c359de")

    def test_rejects_unknown_or_non_sha_git_values(self) -> None:
        self.assertIsNone(release_metadata({"APP_GIT_SHA": "unknown"})["git_sha"])
        self.assertIsNone(release_metadata({"APP_GIT_SHA": "release-candidate"})["git_sha"])


if __name__ == "__main__":
    unittest.main()
