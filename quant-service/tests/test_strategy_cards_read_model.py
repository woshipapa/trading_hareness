"""Strategy cards: the vendor's 十路战法 fields, rebuilt on our own ledger lines."""

from __future__ import annotations

import unittest
from datetime import date, datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from app.strategy_cards_read_model import LINES, strategy_cards

CN = ZoneInfo("Asia/Shanghai")
SESSION, AS_OF = date(2026, 10, 9), date(2026, 10, 8)


def at(hour, minute):
    return datetime(2026, 10, 9, hour, minute, tzinfo=CN)


def snapshot(price, pct, turnover, open_=None, prev=None):
    return {"price": price, "pct_change": pct, "turnover": turnover,
            "raw": {"open_price": open_, "prev_price": prev}}


LEDGER = [
    {"strategy_key": "launch_radar", "symbol": "301000.SZ", "rank": 1, "raw_score": 88, "score_scale": "0-100",
     "liquidity_eligible": True, "liquidity_flags": [], "evidence": {}},
    {"strategy_key": "launch_radar", "symbol": "600001.SH", "rank": 2, "raw_score": 70, "score_scale": "0-100",
     "liquidity_eligible": True, "liquidity_flags": [], "evidence": {}},
    {"strategy_key": "post_close_base_ready", "symbol": "301000.SZ", "rank": 3, "raw_score": 0.4, "score_scale": "pct",
     "liquidity_eligible": True, "liquidity_flags": [], "evidence": {}},
    {"strategy_key": "board_stock_mining_outflow", "symbol": "600002.SH", "rank": 1, "raw_score": -5,
     "score_scale": "cny", "liquidity_eligible": True, "liquidity_flags": [], "evidence": {}},
]
LATEST = {"301000.SZ": snapshot(10.824, 8.24, 5e8, open_=9.712, prev=10.0),
          "600001.SH": snapshot(11.0, 10.0, 2e8, open_=11.0, prev=10.0),
          "600002.SH": snapshot(9.5, -5.0, 1e8, open_=10.0, prev=10.0)}
AUCTION = {"301000.SZ": snapshot(9.712, -2.88, 1.2e7)}
LEADERBOARD = [
    {"strategy_key": "launch_radar", "as_of_date": date(2026, 10, 7), "symbol": "600010.SH",
     "next_session": AS_OF, "open": 10.0, "close": 10.5, "limit_up": 11.0},
    {"strategy_key": "launch_radar", "as_of_date": date(2026, 10, 7), "symbol": "600011.SH",
     "next_session": AS_OF, "open": 11.0, "close": 11.0, "limit_up": 11.0},
    {"strategy_key": "launch_radar", "as_of_date": date(2026, 10, 6), "symbol": "600012.SH",
     "next_session": date(2026, 10, 7), "open": 10.0, "close": 9.9, "limit_up": 11.0},
    {"strategy_key": "board_stock_mining_outflow", "as_of_date": date(2026, 10, 7), "symbol": "600013.SH",
     "next_session": AS_OF, "open": 10.0, "close": 9.0, "limit_up": 11.0},
]
RADAR_POINT = {"observed_at": at(10, 30).isoformat(), "phase": "continuous",
               "bands": {"2": {"now_up": {"turnover": 300.0}, "now_down": {"turnover": 200.0}}}}
FLOW = {"snapshot_minute": at(10, 30), "observed_at": at(10, 30), "status": "completed",
        "payload": {"providers": {"industry": "longhuvip"},
                    "items": [{"taxonomy_key": "longhu_ths_industry", "net_inflow": 5e8, "unit": "cny"}]}}


class _Connection:
    def __init__(self):
        self.sql = []

    def execute(self, sql, params=()):
        self.sql.append(sql)
        rows = self._rows(sql, params)
        return SimpleNamespace(fetchall=lambda: rows, fetchone=lambda: rows[0] if rows else None)

    def _rows(self, sql, params):
        if "max(as_of_date)" in sql:
            return [{"as_of": AS_OF}]
        if "WITH days AS" in sql:
            return LEADERBOARD
        if "FROM quant.strategy_daily_candidates WHERE as_of_date=%s" in sql:
            return LEDGER
        if "FROM quant.instruments" in sql:
            return [{"symbol": symbol, "name": f"名{symbol[:6]}"} for symbol in params[0]]
        if "unnest(%s::text[]) AS wanted" in sql:
            source = LATEST if "DESC" in sql else AUCTION
            when = at(10, 30) if "DESC" in sql else at(9, 25)
            return [{"symbol": symbol, "effective_at": when, "normalized": source[symbol]}
                    for symbol in params[0] if symbol in source]
        if "FROM quant.daily_trade_limits" in sql:
            return [{"symbol": "600001.SH", "limit_up": 11.0, "limit_down": 9.0},
                    {"symbol": "301000.SZ", "limit_up": 12.0, "limit_down": 8.0}]
        if "FROM quant.intraday_signal_events" in sql:
            return [{"symbol": "301000.SZ", "signal_key": "301000.SZ:entry:launch-radar-v3", "state": "alerted",
                     "score": 0.9, "observed_at": at(9, 41)}]
        if "FROM quant.sector_membership_history" in sql:
            return [{"symbol": "301000.SZ", "sector_key": "885001.TI", "label": "固态电池"},
                    {"symbol": "301000.SZ", "sector_key": "885002.TI", "label": "储能"}]
        if "fuyao_ths_concept_limit_strength" in sql:
            return [{"sector_key": "885002.TI", "strength": 6}, {"sector_key": "885001.TI", "strength": 2}]
        if "FROM quant.disclosure_schedule" in sql:
            return [{"symbol": "600001.SH", "period": "2026Q3"}]
        if "FROM quant.market_events" in sql:
            return [{"symbol": "301000.SZ"}]
        if "FROM quant.raw_market_observations" in sql and "symbol IS NULL" in sql:
            return [{"normalized": RADAR_POINT}]
        if "intraday_board_flow_snapshots" in sql:
            return [FLOW]
        if "market_regime_daily" in sql:
            return [{"regime_label": "mixed_transition", "model_version": "v1"}]
        return []


class StrategyCardTests(unittest.TestCase):
    def setUp(self):
        self.connection = _Connection()
        self.day = strategy_cards(self.connection, SESSION, per_line=10)
        self.cards = {card["strategy_key"]: card for card in self.day["cards"]}

    def test_the_large_percentage_is_the_return_from_the_open(self):
        pick = self.cards["launch_radar"]["picks"][0]
        self.assertEqual((pick["open_pct"], pick["latest_pct"], pick["since_open_pct"]), (-2.88, 8.24, 11.4498))
        self.assertEqual(pick["auction"]["pct_change"], -2.88)
        self.assertEqual(pick["auction"]["turnover"], 1.2e7)

    def test_a_ticket_resonance_themes_and_events_per_pick(self):
        pick = self.cards["launch_radar"]["picks"][0]
        self.assertTrue(pick["ticket"])
        self.assertEqual(pick["entry_signals"][0]["model"], "launch-radar-v3")
        self.assertEqual((pick["resonance"], sorted(pick["resonance_lines"])), (2, ["launch_radar", "post_close_base_ready"]))
        self.assertEqual([theme["label"] for theme in pick["themes"]], ["储能", "固态电池"], "strongest concept first")
        self.assertEqual([event["kind"] for event in pick["events"]], ["lhb"])
        self.assertEqual(self.cards["launch_radar"]["picks"][1]["events"][0]["kind"], "disclosure_due")

    def test_a_pick_that_opened_at_its_limit_is_not_averaged(self):
        card = self.cards["launch_radar"]
        self.assertTrue(card["picks"][1]["opened_at_limit_up"])
        self.assertEqual(card["summary"]["scored"], 1)
        self.assertEqual(card["summary"]["mean_since_open_pct"], 11.4498)
        self.assertEqual(card["summary"]["tickets"], 1)

    def test_an_outflow_line_is_scored_short(self):
        card = self.cards["board_stock_mining_outflow"]
        self.assertEqual(card["direction"], "short")
        self.assertEqual(card["summary"]["mean_since_open_pct"], 5.0)
        self.assertEqual(card["leaderboard"]["5"]["mean_open_to_close_pct"], 10.0)

    def test_the_leaderboard_scores_settled_days_and_counts_unbuyable_opens_apart(self):
        five = self.cards["launch_radar"]["leaderboard"]["5"]
        self.assertEqual((five["picks"], five["scored"], five["opened_at_limit"]), (3, 2, 1))
        self.assertEqual(five["mean_open_to_close_pct"], 2.0)
        self.assertEqual(five["rose_share"], 0.5)
        self.assertEqual([item["strategy_key"] for item in self.day["leaderboard"]["5"]],
                         ["board_stock_mining_outflow", "launch_radar"])

    def test_cards_follow_the_line_order_and_name_their_contract(self):
        order = [card["strategy_key"] for card in self.day["cards"]]
        self.assertEqual(order, sorted(order, key=list(LINES).index))
        self.assertEqual(self.cards["launch_radar"]["contract_key"], "launch_radar")
        self.assertEqual(self.cards["launch_radar"]["maturity"], "shadow")
        self.assertEqual(self.day["live_effect"], "none")

    def test_the_direction_gate_reads_the_radar_and_the_main_net(self):
        gate = self.day["direction_gate"]
        self.assertEqual((gate["label"], gate["up_down_ratio"], gate["main_net"]), ("up", 1.5, 5e8))
        self.assertEqual(self.day["previous_close_regime"]["regime_label"], "mixed_transition")

    def test_quotes_are_read_with_one_index_probe_per_symbol(self):
        self.assertFalse(any("DISTINCT ON (symbol) symbol,effective_at" in sql for sql in self.connection.sql))

    def test_no_ledger_is_missing_not_empty(self):
        class Empty(_Connection):
            def _rows(self, sql, params):
                return [{"as_of": None}] if "max(as_of_date)" in sql else []
        day = strategy_cards(Empty(), SESSION)
        self.assertEqual((day["status"], day["cards"]), ("missing", []))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
