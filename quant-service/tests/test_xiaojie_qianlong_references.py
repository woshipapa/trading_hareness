"""Row mapping for the 潜龙 session references and the board-flow read."""

from __future__ import annotations

import unittest
from datetime import date, datetime, timezone
from unittest.mock import MagicMock, patch

from app.xiaojie_reference_repository import (
    BOARD_FLOW_TAXONOMY, QIANLONG_MARKER_VOLUME_RATIO, latest_board_flow, load_session_reference,
    qianlong_references,
)


def _connection(fetchall=None, fetchone=None):
    connection = MagicMock()
    connection.execute.return_value.fetchall.return_value = fetchall or []
    connection.execute.return_value.fetchone.return_value = fetchone
    return connection


class QianlongReferenceTests(unittest.TestCase):
    ROW = {"symbol": "001216.SZ", "sessions": 60, "close_5_sessions_before": 14.94, "ma10": 17.44,
           "high_60d": 24.06, "ma_spread_min_10d_pct": 0.8, "marker_k_sessions_ago": 2,
           "marker_box_top": 19.88, "marker_box_range_pct": 39.61, "fundamental_pe": 34.52,
           "fundamental_date": date(2026, 9, 21), "fundamental_provider": "longhuvip_composite"}

    def test_rows_map_to_named_inputs_with_their_fundamental_source(self):
        connection = _connection([self.ROW])
        refs = qianlong_references(connection, date(2026, 9, 22))
        ref = refs["001216.SZ"]
        self.assertTrue(ref["daily_history_complete"])
        self.assertEqual(ref["marker_k_sessions_ago"], 2)
        self.assertEqual(ref["fundamental"], {"pe": 34.52, "trading_date": "2026-09-21",
                                              "provider": "longhuvip_composite"})
        sql, params = connection.execute.call_args.args
        self.assertIn("trading_date < %s", sql)          # completed sessions only
        self.assertIn(QIANLONG_MARKER_VOLUME_RATIO, params)

    def test_absent_values_stay_absent(self):
        row = {**self.ROW, "sessions": 12, "marker_k_sessions_ago": None, "marker_box_top": None,
               "marker_box_range_pct": None, "fundamental_pe": None, "fundamental_date": None,
               "fundamental_provider": None, "close_5_sessions_before": None}
        ref = qianlong_references(_connection([row]), date(2026, 9, 22))["001216.SZ"]
        self.assertFalse(ref["daily_history_complete"])   # a short history cannot prove "no marker"
        self.assertIsNone(ref["marker_k_sessions_ago"])
        self.assertIsNone(ref["close_5_sessions_before"])
        self.assertEqual(ref["fundamental"], {"pe": None, "trading_date": None, "provider": None})

    def test_the_session_reference_merges_them_into_the_per_symbol_references(self):
        with patch("app.xiaojie_reference_repository.trade_limits", return_value={}), \
             patch("app.xiaojie_reference_repository.sector_membership", return_value={}), \
             patch("app.xiaojie_reference_repository.sector_membership_taxonomy", return_value=None), \
             patch("app.xiaojie_reference_repository.candidate_references",
                   return_value={"001216.SZ": {"ma20": 17.0}}), \
             patch("app.xiaojie_reference_repository.qianlong_references",
                   return_value={"001216.SZ": {"high_60d": 24.06}, "600000.SH": {"high_60d": 9.0}}), \
             patch("app.xiaojie_reference_repository.market_volume_baseline", return_value=None), \
             patch("app.xiaojie_reference_repository.instrument_names", return_value={}):
            reference = load_session_reference(MagicMock(), date(2026, 9, 22))
        self.assertEqual(reference["references"]["001216.SZ"], {"ma20": 17.0, "high_60d": 24.06})
        self.assertEqual(reference["references"]["600000.SH"], {"high_60d": 9.0})


class LatestBoardFlowTests(unittest.TestCase):
    UNTIL = datetime(2026, 9, 22, 6, 0, tzinfo=timezone.utc)

    def test_only_boards_sharing_the_membership_taxonomy_are_kept(self):
        row = {"observed_at": datetime(2026, 9, 22, 5, 59, tzinfo=timezone.utc),
               "providers": {"industry": "longhuvip", "concept": "eastmoney_free"},
               "items": [{"taxonomy_key": BOARD_FLOW_TAXONOMY, "sector_key": "881164", "label": "文化传媒",
                          "change_pct": 3.1, "net_inflow": 2.8e9, "amount": 4.4e10},
                         {"taxonomy_key": "eastmoney_concept", "sector_key": "AI视频", "change_pct": 2.2,
                          "net_inflow": 33.0}]}
        flow = latest_board_flow(_connection(fetchone=row), date(2026, 9, 22), self.UNTIL)
        self.assertEqual(flow["status"], "stored")
        self.assertEqual(flow["provider"], "longhuvip")
        self.assertEqual(list(flow["boards"]), ["881164"])
        self.assertEqual(flow["boards"]["881164"]["amount"], 4.4e10)

    def test_no_point_yet_is_missing_not_empty_success(self):
        self.assertEqual(latest_board_flow(_connection(fetchone=None), date(2026, 9, 22), self.UNTIL)["status"],
                         "missing")
        concept_only = {"observed_at": self.UNTIL, "providers": {},
                        "items": [{"taxonomy_key": "eastmoney_concept", "sector_key": "X", "net_inflow": 1.0}]}
        self.assertEqual(latest_board_flow(_connection(fetchone=concept_only), date(2026, 9, 22),
                                           self.UNTIL)["status"], "missing")


if __name__ == "__main__":
    unittest.main()
