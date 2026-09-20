from pathlib import Path
import unittest


SCRIPT = Path(__file__).with_name("shared-peer") / "ensure-batch-tunnel.sh"


class EnsureBatchTunnelScriptTests(unittest.TestCase):
    def test_helper_builds_only_the_bulk_sidecar(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertIn('"${compose[@]}" build db-batch-tunnel', source)
        self.assertIn('"${compose[@]}" up -d --no-build db-batch-tunnel', source)
        self.assertIn('"scheduler_unchanged":true', source)
        self.assertNotIn("up -d --no-build db-batch-tunnel quant-research-scheduler", source)

    def test_helper_has_dry_run_and_disk_guard(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertIn('DRY_RUN:-0', source)
        self.assertIn("PEER_BATCH_BUILD_MIN_FREE_KB", source)
        self.assertIn("insufficient free space", source)


if __name__ == "__main__":
    unittest.main()
