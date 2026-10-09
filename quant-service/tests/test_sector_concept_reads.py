"""Concept reads name the taxonomy they served after the Tushare retirement."""

from __future__ import annotations

import unittest
from datetime import date

from app.sector_read_model import (
    CANDIDATE_TAXONOMIES, CONCEPT_FLOW_TAXONOMIES, concept_candidate_source, concept_flow_source,
    project_membership_refresh_status, select_taxonomy, superseded_notice,
)


class TaxonomySelectionTests(unittest.TestCase):
    def test_the_newest_session_wins_and_a_tie_goes_to_the_replacement(self):
        self.assertEqual(select_taxonomy({"eastmoney_concept": date(2026, 10, 9), "ths_concept_flow": date(2026, 10, 7)},
                                         CONCEPT_FLOW_TAXONOMIES), ("eastmoney_concept", date(2026, 10, 9)))
        self.assertEqual(select_taxonomy({"fuyao_ths_concept": date(2026, 10, 7), "ths_concept_flow": date(2026, 10, 7)},
                                         CANDIDATE_TAXONOMIES), ("fuyao_ths_concept", date(2026, 10, 7)))

    def test_a_session_only_the_tushare_history_holds_is_still_readable(self):
        self.assertEqual(select_taxonomy({"ths_concept_flow": date(2026, 9, 30)}, CONCEPT_FLOW_TAXONOMIES),
                         ("ths_concept_flow", date(2026, 9, 30)))

    def test_no_rows_names_the_replacement_without_a_date(self):
        self.assertEqual(select_taxonomy({}, CONCEPT_FLOW_TAXONOMIES), ("eastmoney_concept", None))

    def test_sources_never_present_another_vendor_as_ths(self):
        eastmoney = concept_flow_source("eastmoney_concept")
        self.assertEqual((eastmoney["requested_taxonomy_key"], eastmoney["strength"]["status"]),
                         ("ths_concept_flow", "not_joined"))
        self.assertNotIn("ths", eastmoney["source"])
        self.assertEqual(concept_flow_source("ths_concept_flow")["strength"]["taxonomy_key"], "ths_limit_strength")
        self.assertIn("fuyao", concept_candidate_source("fuyao_ths_concept")["source"])
        self.assertIn("retired", concept_candidate_source("ths_concept_flow")["source"])

    def test_retired_flow_taxonomies_name_their_replacement(self):
        self.assertEqual(superseded_notice("ths_industry")["superseded_by"], "longhu_ths_industry")
        self.assertEqual(superseded_notice("ths_concept_flow")["superseded_by"], "eastmoney_concept")
        self.assertEqual(superseded_notice("longhu_ths_industry"), {})


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
