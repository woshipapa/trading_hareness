from __future__ import annotations

from datetime import date, datetime, timezone
import unittest
from unittest.mock import MagicMock

from app.post_close_evidence_repository import lhb_event_rows, load_exact_board_context_rows, load_lhb_context_rows


class PostCloseEvidenceRepositoryTests(unittest.TestCase):
    def test_exact_context_falls_back_to_saved_longhu_industry_membership(self) -> None:
        database = MagicMock()
        connection = MagicMock()
        database.transaction.return_value.__enter__.return_value = connection
        result = MagicMock()
        result.fetchall.return_value = [{
            "symbol": "600000.SH", "sector_key": "881181", "label": "银行",
            "net_amount": 100, "provider_key": "longhuvip_composite",
            "taxonomy_key": "longhu_ths_industry",
        }]
        connection.execute.return_value = result

        rows = load_exact_board_context_rows(database, date(2026, 9, 1))

        self.assertEqual(rows[0]["provider_key"], "longhuvip_composite")
        query, params = connection.execute.call_args.args
        self.assertIn("stock.raw->>'plate_id'", query)
        self.assertIn("jsonb_array_elements", query)
        self.assertEqual(params, (date(2026, 9, 1),) * 6)

    def test_dragon_tiger_rows_are_keyed_by_the_lists_own_trade_date(self) -> None:
        database = MagicMock()
        connection = MagicMock()
        database.transaction.return_value.__enter__.return_value = connection
        connection.execute.return_value.fetchall.return_value = [{
            "symbol": "601086.SH", "source": "fuyao_ths",
            "available_at": datetime(2026, 9, 17, 9, 40, tzinfo=timezone.utc),
            "payload": {"trade_date": "2026-09-17", "net_value": 12599059.02},
        }]

        rows = load_lhb_context_rows(database, date(2026, 9, 17))

        self.assertEqual(rows[0]["payload"]["net_value"], 12599059.02)
        query, params = connection.execute.call_args.args
        self.assertIn("event_type='lhb_ths'", query)
        self.assertIn("CASE WHEN body IS JSON THEN body::jsonb END", query)
        self.assertIn("payload->>'trade_date'=%s", query)
        self.assertNotIn("tushare_raw_records", query)
        # A post-close read of the session sees every capture of its list.
        self.assertEqual(params, (None, None, "2026-09-17"))

    def test_dragon_tiger_rows_can_be_bounded_by_a_checkpoint(self) -> None:
        connection = MagicMock()
        connection.execute.return_value.fetchall.return_value = []
        checkpoint = datetime(2026, 9, 17, 7, 30, tzinfo=timezone.utc)

        self.assertEqual(lhb_event_rows(connection, date(2026, 9, 17), available_by=checkpoint), [])

        query, params = connection.execute.call_args.args
        self.assertIn("available_at<=%s", query)
        self.assertEqual(params, (checkpoint, checkpoint, "2026-09-17"))


if __name__ == "__main__":
    unittest.main()
