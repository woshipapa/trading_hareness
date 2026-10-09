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
        self.assertEqual(rows[0]["net_amount_unit"], "yuan")
        query, params = next(call.args for call in connection.execute.call_args_list if "plate_id" in call.args[0])
        self.assertIn("stock.raw->>'plate_id'", query)
        self.assertIn("jsonb_array_elements", query)
        self.assertEqual(params, (date(2026, 9, 1),) * 6)

    def test_live_ths_concept_flow_reaches_fuyao_members_by_name_and_covers_the_longhu_fallback(self) -> None:
        day = date(2026, 10, 9)

        class Connection:
            def execute(self, sql, params=()):
                rows: list[dict] = []
                if "plate_id" in sql:
                    rows = [{"symbol": "600000.SH", "sector_key": "881181", "label": "银行", "net_amount": 3e8,
                             "provider_key": "longhuvip_composite", "taxonomy_key": "longhu_ths_industry"},
                            {"symbol": "600001.SH", "sector_key": "881181", "label": "银行", "net_amount": 3e8,
                             "provider_key": "longhuvip_composite", "taxonomy_key": "longhu_ths_industry"}]
                elif "taxonomy_key='eastmoney_concept'" in sql:
                    rows = [{"sector_key": "ＰＣＢ", "net_amount": 5.2, "change_pct": 1.1, "leading_label": "甲",
                             "provider_key": "eastmoney_free", "available_at": None},
                            {"sector_key": "改了名的概念", "net_amount": 1.0, "change_pct": 0.1, "leading_label": "乙",
                             "provider_key": "eastmoney_free", "available_at": None}]
                elif "FROM quant.sectors" in sql:
                    rows = [{"taxonomy_key": "fuyao_ths_concept", "sector_key": "885959.TI", "label": "PCB"}]
                elif "taxonomy_key='fuyao_ths_concept'" in sql:
                    assert params[-1] == ["885959.TI"]
                    rows = [{"symbol": "600000.SH", "sector_key": "885959.TI"}]
                return MagicMock(fetchall=MagicMock(return_value=rows))

        database = MagicMock()
        database.transaction.return_value.__enter__.return_value = Connection()
        rows = load_exact_board_context_rows(database, day)
        live = [row for row in rows if row["taxonomy_key"] == "fuyao_ths_concept"]
        self.assertEqual([(row["symbol"], row["sector_key"], row["label"]) for row in live], [("600000.SH", "885959.TI", "PCB")])
        self.assertEqual((live[0]["flow_board_name"], live[0]["net_amount_unit"]), ("ＰＣＢ", "100m_cny"))
        self.assertEqual([row["symbol"] for row in rows if row["taxonomy_key"] == "longhu_ths_industry"], ["600001.SH"],
                         "the industry fallback only fills a symbol no concept covers")

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
