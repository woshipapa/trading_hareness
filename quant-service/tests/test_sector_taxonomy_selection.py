from __future__ import annotations

import unittest
from datetime import date

from app.platform.strategy_data_needs import strategy_taxonomies
from app.xiaojie_reference_repository import (
    MINIMUM_TAXONOMY_COVERAGE,
    _best_membership,
)

TRADING_DATE = date(2026, 9, 21)


class _Connection:
    """Serves one membership map per taxonomy, recording what was asked for."""

    def __init__(self, by_taxonomy):
        self.by_taxonomy = by_taxonomy
        self.asked: list[str] = []
        self.statements: list[str] = []
        self.parameters: list[tuple] = []

    def execute(self, statement, parameters=None):
        taxonomy = parameters[0] if parameters else None
        self.asked.append(taxonomy)
        self.statements.append(statement)
        self.parameters.append(parameters or ())
        rows = [{"symbol": symbol, "sector_key": sector}
                for symbol, sectors in self.by_taxonomy.get(taxonomy, {}).items()
                for sector in sectors]
        return _Result(rows)


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


def _map(count: int, sectors_each: int, prefix: str) -> dict[str, list[str]]:
    return {f"{600000 + index}.SH": [f"{prefix}{slot}" for slot in range(sectors_each)]
            for index in range(count)}


class SectorTaxonomySelectionTests(unittest.TestCase):
    """An industry map and a concept map are not interchangeable.

    Selection was by coverage while only one taxonomy held anything.  Once the
    2026-09-18 membership load filled both, ths_concept_flow led by 254 names
    (5,569 vs 5,315) and would have replaced one industry per stock with 23.6
    concepts - leaving leader-flow's "is this name leading its sector" with no
    answer at all.
    """

    def test_the_declared_order_wins_even_when_the_other_covers_more(self):
        connection = _Connection({
            "longhu_ths_industry": _map(5315, 1, "ind"),
            "ths_concept_flow": _map(5569, 24, "con"),
        })
        key, membership = _best_membership(connection, TRADING_DATE)
        self.assertEqual(key, "longhu_ths_industry")
        self.assertEqual(len(membership), 5315)
        # The preferred map cleared the floor, so the wider one is never read.
        self.assertEqual(connection.asked, ["longhu_ths_industry"])

    def test_an_unloaded_preferred_taxonomy_falls_through_to_the_next(self):
        # The state this rule was originally written for: concept flow held 278
        # names for months while the industry map held the market.
        connection = _Connection({
            "longhu_ths_industry": _map(278, 1, "ind"),
            "ths_concept_flow": _map(5569, 24, "con"),
        })
        key, membership = _best_membership(connection, TRADING_DATE)
        self.assertEqual(key, "ths_concept_flow")
        self.assertEqual(len(membership), 5569)

    def test_when_none_clears_the_floor_the_widest_is_still_returned(self):
        connection = _Connection({
            "longhu_ths_industry": _map(120, 1, "ind"),
            "ths_concept_flow": _map(400, 24, "con"),
        })
        key, membership = _best_membership(connection, TRADING_DATE)
        self.assertEqual(key, "ths_concept_flow")
        self.assertEqual(len(membership), 400)

    def test_an_empty_session_names_no_taxonomy(self):
        key, membership = _best_membership(_Connection({}), TRADING_DATE)
        self.assertIsNone(key)
        self.assertEqual(membership, {})

    def test_leader_flow_declares_the_industry_map_first(self):
        self.assertEqual(strategy_taxonomies("xiaojie_leader_flow")[0], "longhu_ths_industry")

    def test_the_floor_sits_below_a_full_market_and_above_a_stub(self):
        self.assertLess(MINIMUM_TAXONOMY_COVERAGE, 5000)
        self.assertGreater(MINIMUM_TAXONOMY_COVERAGE, 500)


class SectorOnlyMembershipTests(unittest.TestCase):
    """A qualification list is not a sector, on the fallback path too.

    The concept taxonomy leader-flow falls back to carries 融资融券 with 3,915
    members.  Subtracting "everything margin-eligible" from a name's move
    measures the market, not a sector rotation, so the same exclusion the peer
    and exposure reads use applies here.
    """

    def test_the_membership_read_excludes_the_catalogued_non_sectors(self):
        from app.datasources.catalog import NON_SECTOR_GROUPS
        from app.xiaojie_reference_repository import membership_for_taxonomy

        connection = _Connection({"ths_concept_flow": _map(10, 2, "con")})
        membership_for_taxonomy(connection, TRADING_DATE, "ths_concept_flow")
        statement = connection.statements[0]
        self.assertIn("non_sector", statement)
        self.assertIn("sector_key = ANY", statement)
        # The keys travel as a parameter, so the catalog stays the one source.
        self.assertIn(list(NON_SECTOR_GROUPS), [list(value) for value in connection.parameters[0]
                                                if isinstance(value, list)])


if __name__ == "__main__":
    unittest.main()
