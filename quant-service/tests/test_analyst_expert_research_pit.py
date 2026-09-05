from __future__ import annotations

from pathlib import Path
import unittest


class AnalystExpertResearchPitTests(unittest.TestCase):
    def test_settlement_queries_require_fresh_session_available_bars(self) -> None:
        source = Path("app/analyst_expert_research.py").read_text(encoding="utf-8")
        self.assertGreaterEqual(source.count("quality_status='fresh'"), 5)
        self.assertGreaterEqual(
            source.count("available_at < ((trading_date+1)::timestamp AT TIME ZONE 'Asia/Shanghai')"),
            3,
        )
        self.assertIn("m.known_at<=%s", source)


if __name__ == "__main__":
    unittest.main()
