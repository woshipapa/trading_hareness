from contextlib import contextmanager
import unittest

from app.longhu_replay_read_repository import readiness


class _Result:
    def __init__(self, rows):
        self.rows = rows

    def fetchall(self):
        return self.rows


class LonghuReplayReadRepositoryTests(unittest.TestCase):
    def test_readiness_uses_one_transaction_and_separate_timeout_statement(self):
        class Connection:
            def __init__(self):
                self.calls = []

            def execute(self, statement):
                self.calls.append(statement)
                return _Result([])

        class Database:
            def __init__(self):
                self.connection = Connection()

            @contextmanager
            def transaction(self):
                yield self.connection

        database = Database()
        result = readiness(database)
        self.assertEqual(result["status"], "accumulating")
        self.assertEqual(len(database.connection.calls), 2)
        self.assertTrue(database.connection.calls[0].startswith("SET LOCAL statement_timeout"))
        self.assertIn("FROM quant.raw_market_observations", database.connection.calls[1])
