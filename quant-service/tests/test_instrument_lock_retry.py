import unittest

from app.instrument_lock_retry import is_retryable_instrument_error, run_instrument_write_with_retry


class _PgError(Exception):
    def __init__(self, state):
        super().__init__(state)
        self.sqlstate = state


class _Connection:
    def __init__(self):
        self.statements = []

    def execute(self, statement, parameters=None):
        self.statements.append(statement)


class InstrumentLockRetryTests(unittest.TestCase):
    def test_only_lock_timeout_and_deadlock_are_retryable(self):
        self.assertTrue(is_retryable_instrument_error(_PgError("55P03")))
        self.assertTrue(is_retryable_instrument_error(_PgError("40P01")))
        self.assertFalse(is_retryable_instrument_error(_PgError("23505")))

    def test_retries_inside_savepoints_and_releases_on_success(self):
        connection = _Connection()
        calls = []
        clock = iter([0.0, 0.01, 0.02, 0.03])
        def operation():
            calls.append(len(calls))
            if len(calls) < 3:
                raise _PgError("55P03")
            return 17
        result = run_instrument_write_with_retry(
            connection, operation, sleep=lambda _delay: None,
            monotonic=lambda: next(clock, 0.03), total_timeout_seconds=1,
        )
        self.assertEqual(result, 17)
        self.assertEqual(len(calls), 3)
        self.assertEqual(connection.statements.count("SET LOCAL lock_timeout = '300ms'"), 3)
        self.assertEqual(sum(statement.startswith("ROLLBACK TO SAVEPOINT") for statement in connection.statements), 2)
        self.assertEqual(sum(statement.startswith("RELEASE SAVEPOINT") for statement in connection.statements), 3)

    def test_non_lock_error_is_not_retried(self):
        connection = _Connection()
        calls = []
        def operation():
            calls.append(1)
            raise _PgError("23505")
        with self.assertRaises(_PgError):
            run_instrument_write_with_retry(connection, operation, sleep=lambda _delay: None)
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
