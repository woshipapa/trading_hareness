import unittest
from contextlib import contextmanager

from app.longhu_schema_profile_repository import schema_profile


class _Result:
    def __init__(self, rows):
        self.rows = rows

    def fetchall(self):
        return self.rows


class LonghuSchemaProfileRepositoryTests(unittest.TestCase):
    def test_repository_profiles_nested_payload_without_returning_value(self):
        class Connection:
            def __init__(self): self.calls = []
            def execute(self, statement, params=()):
                self.calls.append((statement, params))
                return _Result([{"target": "longhu_article", "action": "GetList", "payload": {"Title": "private"}}])

        class Database:
            def __init__(self): self.connection = Connection()
            @contextmanager
            def transaction(self): yield self.connection

        database = Database()
        result = schema_profile(database)
        self.assertEqual(result["status"], "observed")
        self.assertNotIn("private", str(result))
        self.assertEqual(len(database.connection.calls), 2)
