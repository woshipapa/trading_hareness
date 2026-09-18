import unittest
from datetime import date
from types import SimpleNamespace

from app.xiaojie_reference_repository import (
    SECTOR_TAXONOMY_PREFERENCE,
    sector_membership,
    sector_membership_taxonomy,
)

TRADING_DATE = date(2026, 9, 18)


class _Connection:
    """Answers membership rows per taxonomy and records what was asked."""

    def __init__(self, by_taxonomy):
        self.by_taxonomy, self.asked = by_taxonomy, []

    def execute(self, _statement, values):
        taxonomy = values[0]
        self.asked.append(taxonomy)
        rows = [{"symbol": symbol, "sector_key": sector}
                for symbol, sector in self.by_taxonomy.get(taxonomy, [])]
        return SimpleNamespace(fetchall=lambda: rows)


class SectorMembershipPreferenceTests(unittest.TestCase):
    """With no membership at all the strategy rejects every candidate, so the
    read falls through to whichever taxonomy can actually answer."""

    def test_equal_coverage_keeps_the_leading_taxonomy(self):
        connection = _Connection({
            "ths_concept_flow": [("600176.SH", "885556.TI")],
            "longhu_ths_industry": [("600176.SH", "881121")],
        })
        self.assertEqual(sector_membership(connection, TRADING_DATE),
                         {"600176.SH": {"885556.TI"}})

    def test_the_longhu_industry_taxonomy_answers_when_concept_is_empty(self):
        connection = _Connection({"longhu_ths_industry": [("600176.SH", "881121")]})
        self.assertEqual(sector_membership(connection, TRADING_DATE),
                         {"600176.SH": {"881121"}})
        self.assertEqual(connection.asked, list(SECTOR_TAXONOMY_PREFERENCE))

    def test_a_one_board_remnant_does_not_beat_a_full_market_map(self):
        # 2026-09-18: an interrupted backfill left 278 concept symbols against
        # Longhu's 5,315. Taking the first non-empty taxonomy would have handed
        # the strategy the one-board map, which leaves every pool member
        # sector_core_unconfirmed exactly as an empty table does.
        connection = _Connection({
            "ths_concept_flow": [(f"{600000 + i}.SH", "885556.TI") for i in range(278)],
            "longhu_ths_industry": [(f"{600000 + i}.SH", "881121") for i in range(5315)],
        })
        self.assertEqual(len(sector_membership(connection, TRADING_DATE)), 5315)
        self.assertEqual(sector_membership_taxonomy(connection, TRADING_DATE), "longhu_ths_industry")

    def test_no_taxonomy_with_rows_yields_no_membership(self):
        connection = _Connection({})
        self.assertEqual(sector_membership(connection, TRADING_DATE), {})

    def test_an_explicit_taxonomy_never_falls_back(self):
        # A caller asking for one vendor's definition gets exactly it, or none.
        connection = _Connection({"longhu_ths_industry": [("600176.SH", "881121")]})
        self.assertEqual(sector_membership(connection, TRADING_DATE, "ths_concept_flow"), {})
        self.assertEqual(connection.asked, ["ths_concept_flow"])

    def test_a_symbol_in_several_sectors_keeps_all_of_them(self):
        connection = _Connection({"ths_concept_flow": [
            ("600176.SH", "885556.TI"), ("600176.SH", "885901.TI")]})
        self.assertEqual(sector_membership(connection, TRADING_DATE),
                         {"600176.SH": {"885556.TI", "885901.TI"}})

    def test_the_source_taxonomy_is_reported_for_the_evidence(self):
        connection = _Connection({"longhu_ths_industry": [("600176.SH", "881121")]})
        self.assertEqual(sector_membership_taxonomy(connection, TRADING_DATE),
                         "longhu_ths_industry")

    def test_no_membership_reports_no_source(self):
        self.assertIsNone(sector_membership_taxonomy(_Connection({}), TRADING_DATE))


if __name__ == "__main__":
    unittest.main()
