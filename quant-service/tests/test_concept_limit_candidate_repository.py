from __future__ import annotations

import json
from datetime import date, datetime, timezone
import unittest

from app.concept_limit_candidate_repository import concept_memberships, latest_limit_up_pool, persist_candidates
from app.datasources.catalog import NON_SECTOR_GROUPS

DAY = date(2026, 10, 9)
CLOSE = datetime(2026, 10, 9, 7, 0, tzinfo=timezone.utc)


class _Result:
    def __init__(self, row=None, rows=None):
        self._row, self._rows = row, rows or []

    def fetchone(self):
        return self._row

    def fetchall(self):
        return self._rows


class _Cursor:
    def __init__(self, connection):
        self.connection = connection

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def executemany(self, sql, rows):
        self.connection.many.append((sql, list(rows)))


class _Connection:
    def __init__(self, answers):
        self.answers, self.calls, self.many = answers, [], []

    def execute(self, sql, params=()):
        self.calls.append((sql, params))
        for marker, result in self.answers:
            if marker in sql:
                return result
        return _Result()

    def cursor(self):
        return _Cursor(self)


class _Database:
    def __init__(self, connection):
        self.connection = connection

    def transaction(self):
        connection = self.connection

        class Context:
            def __enter__(self):
                return connection

            def __exit__(self, *_args):
                return False

        return Context()


class LimitPoolSnapshotTests(unittest.TestCase):
    def test_the_last_captured_minute_of_the_session_is_the_pool(self):
        body = json.dumps({"capability": "a_share_limit_up_pool", "thscode": "600127.SH", "name": "金健米业",
                           "continue_day_cnt": 2, "max_seal_money": 215363530.68}, ensure_ascii=False)
        connection = _Connection([
            ("max(occurred_at) at FROM quant.market_events WHERE event_type='limit_up_pool' AND source='fuyao_ths' AND occurred_at>=",
             _Result({"at": CLOSE})),
            ("SELECT symbol,body", _Result(rows=[{"symbol": "600127.SH", "body": body}])),
        ])
        selected, snapshot_at, pool = latest_limit_up_pool(_Database(connection), DAY)
        self.assertEqual((selected, snapshot_at), (DAY, CLOSE))
        self.assertEqual(pool["600127.SH"]["continue_day_cnt"], 2)
        window = connection.calls[0][1]
        self.assertEqual(window[0].isoformat(), "2026-10-09T00:00:00+08:00")
        self.assertEqual(connection.calls[1][1], (CLOSE,))

    def test_without_a_date_the_newest_captured_session_is_used(self):
        connection = _Connection([
            ("occurred_at>=", _Result({"at": None})),
            ("max(occurred_at) at FROM", _Result({"at": datetime(2026, 10, 8, 7, 0, tzinfo=timezone.utc)})),
        ])
        selected, snapshot_at, pool = latest_limit_up_pool(_Database(connection), None)
        self.assertEqual(selected, date(2026, 10, 8))
        self.assertIsNone(snapshot_at)
        self.assertEqual(pool, {})


class ConceptMembershipTests(unittest.TestCase):
    def test_membership_is_point_in_time_and_sector_only(self):
        connection = _Connection([
            ("SELECT DISTINCT member.sector_key", _Result(rows=[{"sector_key": "885431.TI", "symbol": "000981.SZ"}])),
            ("count(DISTINCT member.symbol)", _Result(rows=[{"sector_key": "885431.TI", "members": 120}])),
            ("SELECT sector_key,label", _Result(rows=[{"sector_key": "885431.TI", "label": "新能源汽车"}])),
        ])
        rows, counts, labels = concept_memberships(_Database(connection), DAY, ["000981.SZ"])
        self.assertEqual((rows[0]["sector_key"], counts, labels),
                         ("885431.TI", {"885431.TI": 120}, {"885431.TI": "新能源汽车"}))
        sql, params = connection.calls[0]
        self.assertIn("member.known_at <=", sql)
        self.assertIn("NOT (member.sector_key = ANY(%s))", sql)
        self.assertEqual(params[:5], ("fuyao_ths_concept", ["000981.SZ"], DAY, DAY, DAY))
        self.assertIn(list(NON_SECTOR_GROUPS), list(params))
        self.assertEqual(sql.count("%s"), len(params))


class PersistCandidateTests(unittest.TestCase):
    def test_the_session_is_replaced_and_rows_keep_their_evidence(self):
        connection = _Connection([])
        pool = {"000981.SZ": {"name": "山子高科", "continue_day_cnt": 3, "max_seal_money": 8.0e7, "limit_up_reason": "汽车拆解"},
                "002528.SZ": {"name": "英飞拓", "max_seal_money": 3.0e7}}
        concepts = [{"sector_key": "885431.TI", "label": "新能源汽车", "rank": 1, "limit_up_count": 2, "member_count": 120,
                     "limit_up_ratio": 0.016667, "max_board_count": 3, "limit_up_symbols": ["000981.SZ", "002528.SZ"]}]
        stored, per_concept = persist_candidates(
            _Database(connection), DAY, CLOSE, concepts, pool, 1, datetime(2026, 10, 9, 10, 35, tzinfo=timezone.utc),
        )
        self.assertEqual(stored, 1)
        self.assertEqual(per_concept[0]["stored"], 1)
        self.assertEqual(per_concept[0]["matched_limit_ups"], 2)
        delete_sql, delete_params = connection.calls[0]
        self.assertIn("DELETE FROM quant.sector_limit_candidates", delete_sql)
        self.assertEqual(delete_params, ("fuyao_ths_concept", DAY, "fuyao_ths"))
        (_sql, rows), = connection.many
        row = rows[0]
        self.assertEqual(row[:5], ("fuyao_ths_concept", "885431.TI", "000981.SZ", DAY, "fuyao_ths"))
        self.assertEqual(row[7:11], ("3天3板", "涨停池", 8.0e7, "汽车拆解"))
        evidence = row[-1].obj
        self.assertEqual(evidence["concept"]["limit_up_count"], 2)
        self.assertEqual(evidence["membership_fetch_status"], "completed")
        self.assertEqual(evidence["snapshot_at"], CLOSE.isoformat())


if __name__ == "__main__":
    unittest.main()
