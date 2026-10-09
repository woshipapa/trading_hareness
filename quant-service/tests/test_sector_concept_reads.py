"""Concept reads name the taxonomy they served after the Tushare retirement."""

from __future__ import annotations

import unittest
from datetime import date

from app.sector_read_model import project_membership_refresh_status


class MembershipStatusTests(unittest.TestCase):
    def test_status_keeps_the_concept_shape_and_adds_every_fuyao_taxonomy(self):
        status = project_membership_refresh_status(
            date(2026, 10, 9),
            [{"taxonomy_key": "fuyao_ths_concept", "listed": 390, "done": 380, "failed": 2},
             {"taxonomy_key": "fuyao_ths_region", "listed": 33, "done": 33, "failed": 0}],
            {"mapped_concepts": 388, "member_rows": 61234, "latest_available_at": "2026-10-09T08:40:00+00:00"}, [],
            automatic_enabled=True, batch_size=25,
        )
        self.assertEqual((status["total_concepts"], status["mapped_concepts"], status["receipt_mapped_concepts"]),
                         (390, 388, 380))
        self.assertFalse(status["complete"])
        self.assertEqual(status["taxonomies"]["fuyao_ths_industry"], {"listed": 0, "completed_or_empty": 0, "failed": 0})
        self.assertEqual((status["taxonomy_key"], status["source"]), ("fuyao_ths_concept", "fuyao_ths"))


if __name__ == "__main__":
    unittest.main()
