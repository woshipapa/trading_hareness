"""Turn a time range into a position range of one chat's history.

The private history gateway reads messages by position, not by time.
Positions grow with time but have holes (recalled messages, system events the
account cannot see), so the range is found by bisection over a probe that
returns the first message at or after a position.

Pure: the gateway call is the ``pull_times`` argument, so this is testable
without larkx or a network.
"""

from __future__ import annotations

from typing import Callable

# pull_times(start, end) -> [(position, create_time_seconds), ...] for the
# messages that exist in the inclusive position range, in any order.
PullTimes = Callable[[int, int], list[tuple[int, float]]]

PROBE_WINDOW = 20
MAX_PROBE_SPAN = 2_000
PAGE_SIZE = 50
MAX_EXPORT_POSITIONS = 20_000


class HistoryRangeError(ValueError):
	pass


def _probe(pull_times: PullTimes, position: int, last: int) -> tuple[int, float] | None:
	"""First existing message at or after ``position``; None when there is none up to ``last``.

	The window doubles so a long hole costs a few calls, not hundreds, and it
	gives up after MAX_PROBE_SPAN positions: a hole that large means the
	account cannot read this stretch at all.
	"""
	start = position
	window = PROBE_WINDOW
	while start <= last and start - position < MAX_PROBE_SPAN:
		end = min(last, start + window - 1)
		found = sorted((int(p), float(t)) for p, t in pull_times(start, end) if int(p) >= position)
		if found:
			return found[0]
		start = end + 1
		window = min(window * 2, 400)
	return None


def _first_position_at_or_after(pull_times: PullTimes, first: int, last: int, moment: float, *, strictly_after: bool = False) -> int:
	"""Smallest position whose message time is >= moment (or > moment); last + 1 if none."""
	low, high = first, last + 1
	while low < high:
		middle = (low + high) // 2
		hit = _probe(pull_times, middle, last)
		late_enough = hit is None or (hit[1] > moment if strictly_after else hit[1] >= moment)
		if late_enough:
			high = middle
		else:
			low = hit[0] + 1
	return low


def locate_positions(pull_times: PullTimes, first: int, last: int, from_time: float, to_time: float) -> dict[str, int]:
	"""Inclusive position range whose messages fall in [from_time, to_time]; count 0 if empty."""
	first, last = int(first), int(last)
	if first < 1 or last < first:
		return {"start": 0, "end": -1, "count": 0}
	if to_time < from_time:
		raise HistoryRangeError("结束时间不能早于开始时间")
	start = _first_position_at_or_after(pull_times, first, last, float(from_time))
	end = _first_position_at_or_after(pull_times, max(first, start), last, float(to_time), strictly_after=True) - 1
	if start > last or end < start:
		return {"start": 0, "end": -1, "count": 0}
	count = end - start + 1
	if count > MAX_EXPORT_POSITIONS:
		raise HistoryRangeError(f"该时间范围覆盖 {count} 个位置，超过单次 {MAX_EXPORT_POSITIONS} 的上限，请缩小范围")
	return {"start": start, "end": end, "count": count}


def page_bounds(start: int, end: int, cursor: int | None = None) -> tuple[int, int, int | None]:
	"""(page_start, page_end, next_cursor) for one page of at most PAGE_SIZE positions."""
	page_start = max(int(start), int(cursor)) if cursor is not None else int(start)
	if page_start > int(end):
		return page_start, page_start - 1, None
	page_end = min(int(end), page_start + PAGE_SIZE - 1)
	return page_start, page_end, (page_end + 1 if page_end < int(end) else None)
