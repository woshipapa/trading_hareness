"""Runtime paths that still reached Tushare after its credentials were removed (2026-10-08).

With no TUSHARE_* configuration every Tushare call raises ProviderCallError. Two
intraday paths let it escape: the board report's limit-up anchors (its handler
did not list the error) and the watch scan's ProMax volume fallback (removed).
"""
from __future__ import annotations

import asyncio
import unittest
from datetime import datetime, timezone
from unittest.mock import patch


class LimitUpAnchorTests(unittest.TestCase):
    def test_no_limit_source_degrades_the_anchors_instead_of_failing_the_board_report(self) -> None:
        from app import main

        async def no_limit_source(_day):
            raise main.ProviderCallError("no Tushare provider is configured")

        recorded: list[str] = []

        async def blocking(function, *args, **kwargs):
            recorded.append(function.__name__)

        with patch.object(main, "_xiaojie_session_context", no_limit_source), \
                patch.object(main, "run_database_blocking", blocking):
            result = asyncio.run(main.refresh_intraday_limit_up_anchors(datetime(2026, 10, 9, 2, 0, tzinfo=timezone.utc)))
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(recorded, ["_persist_local_limit_pool_failure"])


class WatchVolumeFallbackTests(unittest.TestCase):
    def test_the_promax_volume_fallback_is_gone(self) -> None:
        from app import main
        from app.intraday_watch_quote_capture import WatchQuoteCaptureDependencies

        self.assertFalse(hasattr(main, "intraday_watch_volume_fallback"))
        self.assertNotIn("watch_volume_fallback", WatchQuoteCaptureDependencies.__dataclass_fields__)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
