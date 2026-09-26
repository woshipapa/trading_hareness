import unittest
from contextlib import contextmanager

from app.research_catalog_read_model import model_registry


class ResearchModelCatalogTests(unittest.TestCase):
    def test_model_registry_is_read_only_and_explicitly_research_only(self):
        class Result:
            def __init__(self, row=None): self.row = row
            def fetchone(self): return self.row
            def fetchall(self): return []

        class Connection:
            def __init__(self, table=True): self.table = table; self.statements = []
            def execute(self, statement):
                self.statements.append(statement)
                if "to_regclass" in statement:
                    return Result({"value": "quant.research_model_registry"} if self.table else {"value": None})
                return Result()

        class Database:
            def __init__(self, table=True): self.connection = Connection(table)
            @contextmanager
            def transaction(self): yield self.connection

        result = model_registry(Database())
        self.assertEqual(result["items"], [])
        self.assertTrue(result["research_only"])
        self.assertEqual(result["live_effect"], "none")
        self.assertEqual(result["registry_status"], "available")

    def test_missing_model_registry_schema_is_explicit_and_not_an_http_500(self):
        class Result:
            def fetchone(self): return {"value": None}

        class Connection:
            def execute(self, _statement): return Result()

        class Database:
            @contextmanager
            def transaction(self): yield Connection()

        result = model_registry(Database())
        self.assertEqual(result["items"], [])
        self.assertEqual(result["registry_status"], "schema_unavailable")
        self.assertEqual(result["live_effect"], "none")
