import unittest

from app.longhu_capability_registry import capability_contract_catalog, capability_contracts


class LonghuCapabilityRegistryTests(unittest.TestCase):
    def test_every_documented_longhu_pair_has_an_explicit_integration_contract(self) -> None:
        rows = capability_contracts()
        self.assertEqual(len(rows), 51)
        self.assertEqual(len({(row.target, row.action) for row in rows}), 51)
        self.assertTrue(all(row.evidence_dataset == "raw_market_observations" for row in rows))
        self.assertTrue(all(row.strategy_eligible is False for row in rows if row.integration != "live_watch"))

    def test_catalog_is_secret_free_and_marks_everything_research_only(self) -> None:
        catalog = capability_contract_catalog()
        self.assertEqual(len(catalog), 51)
        self.assertTrue(all(item["research_only"] for item in catalog))
        self.assertEqual({item["live_effect"] for item in catalog}, {"none"})
        self.assertNotIn("Token", str(catalog))


if __name__ == "__main__":
    unittest.main()
