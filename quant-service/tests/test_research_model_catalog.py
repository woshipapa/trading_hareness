import unittest
from contextlib import contextmanager

from app.research_catalog_read_model import model_registry


class ResearchModelCatalogTests(unittest.TestCase):
    def test_model_registry_is_read_only_and_explicitly_research_only(self):
        class Result:
            def fetchall(self): return []

        class Connection:
            def execute(self, statement):
                self.statement = statement
                return Result()

        class Database:
            def __init__(self): self.connection = Connection()
            @contextmanager
            def transaction(self): yield self.connection

        result = model_registry(Database())
        self.assertEqual(result["items"], [])
        self.assertTrue(result["research_only"])
        self.assertEqual(result["live_effect"], "none")
