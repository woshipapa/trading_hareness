"""Failure bookkeeping of the long TDX probes: an error is written into the run where it happened.

A reading, sample or poll that fails becomes an entry of the run with ``error`` (the class of the exception) and
``message`` (a short text), next to the time its kind of entry carries. The probes count the entries that carry
``error`` and end a run only after --max-consecutive-errors of them in a row.
"""

MAX_CONSECUTIVE_ERRORS = 5
MESSAGE_CHARS = 200


def failure(error):
    return {"error": type(error).__name__, "message": str(error)[:MESSAGE_CHARS]}
