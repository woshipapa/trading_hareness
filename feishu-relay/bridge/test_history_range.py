import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from history_range import HistoryRangeError, MAX_EXPORT_POSITIONS, PAGE_SIZE, locate_positions, page_bounds

DAY = 86_400
T0 = 1_791_500_000  # an arbitrary start, in seconds


def chat(times_by_position):
	"""A fake gateway over {position: create_time}; counts the calls it serves."""
	calls = []
	def pull_times(start, end):
		calls.append((start, end))
		return [(p, t) for p, t in times_by_position.items() if start <= p <= end]
	return pull_times, calls


def brute(times_by_position, from_time, to_time):
	inside = sorted(p for p, t in times_by_position.items() if from_time <= t <= to_time)
	return (inside[0], inside[-1]) if inside else None


class LocatePositionsTests(unittest.TestCase):
	def test_dense_history_finds_the_exact_bounds_in_logarithmic_calls(self):
		history = {p: T0 + p * 60 for p in range(1, 5001)}  # one message a minute
		pull, calls = chat(history)
		found = locate_positions(pull, 1, 5000, T0 + 1000 * 60, T0 + 1999 * 60)
		self.assertEqual((found["start"], found["end"], found["count"]), (1000, 1999, 1000))
		self.assertLess(len(calls), 40)

	def test_holes_from_recalled_or_invisible_messages_do_not_shift_the_bounds(self):
		history = {p: T0 + p * 60 for p in range(1, 2001) if p % 7 and not 800 <= p <= 1100}  # every 7th missing, plus a 300-wide hole
		for from_p, to_p in ((1, 2000), (500, 1500), (790, 1110), (805, 1095), (1101, 1101), (1999, 2000)):
			pull, _ = chat(history)
			found = locate_positions(pull, 1, 2000, T0 + from_p * 60, T0 + to_p * 60)
			expected = brute(history, T0 + from_p * 60, T0 + to_p * 60)
			if expected is None:
				self.assertEqual(found["count"], 0, (from_p, to_p))
			else:
				# the range may start or end on a hole, but must contain exactly the same messages
				inside = sorted(p for p in history if found["start"] <= p <= found["end"])
				self.assertEqual((inside[0], inside[-1]), expected, (from_p, to_p))

	def test_ranges_outside_the_history_are_empty(self):
		history = {p: T0 + p * 60 for p in range(1, 101)}
		pull, _ = chat(history)
		self.assertEqual(locate_positions(pull, 1, 100, T0 - 10 * DAY, T0 - 9 * DAY)["count"], 0)
		self.assertEqual(locate_positions(pull, 1, 100, T0 + 10 * DAY, T0 + 11 * DAY)["count"], 0)
		self.assertEqual(locate_positions(pull, 1, 0, T0, T0 + DAY)["count"], 0)
		self.assertEqual(locate_positions(chat({})[0], 1, 100, T0, T0 + DAY)["count"], 0)

	def test_many_messages_in_the_same_second_are_all_included(self):
		history = {p: T0 + (p // 50) for p in range(1, 301)}  # 50 messages share each second
		pull, _ = chat(history)
		found = locate_positions(pull, 1, 300, T0 + 2, T0 + 3)
		self.assertEqual((found["start"], found["end"]), (100, 199))

	def test_a_reversed_or_oversized_range_is_refused(self):
		history = {p: T0 + p for p in range(1, MAX_EXPORT_POSITIONS + 500)}
		pull, _ = chat(history)
		with self.assertRaises(HistoryRangeError):
			locate_positions(pull, 1, len(history), T0 + 10, T0 + 5)
		with self.assertRaises(HistoryRangeError) as raised:
			locate_positions(pull, 1, len(history), T0, T0 + len(history))
		self.assertIn("缩小范围", str(raised.exception))

	def test_pages_cover_the_range_once_each(self):
		seen, cursor = [], None
		while True:
			page_start, page_end, cursor = page_bounds(1000, 1123, cursor)
			seen += list(range(page_start, page_end + 1))
			if cursor is None:
				break
		self.assertEqual(seen, list(range(1000, 1124)))
		self.assertEqual(page_bounds(1000, 1123)[1] - 1000 + 1, PAGE_SIZE)
		self.assertEqual(page_bounds(1000, 1123, 2000), (2000, 1999, None))


if __name__ == "__main__":
	unittest.main()
