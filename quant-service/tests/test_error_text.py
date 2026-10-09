"""An exception as text is never empty."""

from __future__ import annotations

import asyncio
import unittest

from app.datasources.error_text import error_text


class ErrorTextTests(unittest.TestCase):
    def test_an_empty_message_still_names_the_exception(self):
        self.assertEqual(error_text(asyncio.TimeoutError()), "TimeoutError")
        self.assertEqual(error_text(RuntimeError("not published yet")), "RuntimeError: not published yet")
        self.assertEqual(len(error_text(ValueError("x" * 500), 50)), 50)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
