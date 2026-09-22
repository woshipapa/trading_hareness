"""Hot/cold tiering policy for the evidence the peer writes (declaration only).

The owner database keeps its hot tier on NVMe (the default tablespace) and a
cold tier on a separate disk (``stock_cold``) with cold twins such as
``raw_market_observations_cold``.  By the owner contract only the owner moves
rows between tiers; the peer declares *what* may move and *when*:

* ``realtime`` data is read by same-day live computation (the latest all-A
  snapshot, today's tape).  It stays hot through its session.
* Everything else is evidence for replay and research.  It is copied to the
  cold twin right after its session closes and deleted from hot only once the
  hot window has passed and the cold copy is verified (same observation id
  and payload hash) - the two tiers overlap for the whole window, so no read
  path ever sees a gap and a failed copy never loses data.

The owner job reads this policy from ``GET /api/v1/research/storage-tiering``
and runs outside the session (``transfer_window``) in bounded batches.  The
peer itself never moves or deletes rows here.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

POLICY_VERSION = "peer-storage-tiering-v1"


@dataclass(frozen=True)
class TierRule:
    table: str
    cold_twin: str
    time_column: str
    capability: str | None          # raw_market_observations only
    hot_sessions: int               # sessions kept hot, counting the session itself
    realtime: bool                  # read by same-day live computation
    note: str


_RAW = ("quant.raw_market_observations", "quant.raw_market_observations_cold", "effective_at")

RULES: tuple[TierRule, ...] = (
    # ~1.5 GB a session, ~95% of raw growth; live reads use only the latest snapshot.
    TierRule(*_RAW, "a_share_prices_snapshot", 2, True, "all-A Level-1 snapshot; live reads take the latest capture"),
    TierRule(*_RAW, "watch_scan_tape", 5, True, "per-scan watch tape; read by that day's post-close review"),
    *(TierRule(*_RAW, capability, 5, False, "intraday evidence for replay")
      for capability in ("realtime_quote", "settled_quote", "order_book_quote", "opening_auction_pulse",
                         "board_change_snapshot", "hot_rank_popularity", "hot_rank_surge", "a_share_hot_stock_list",
                         "a_share_skyrocket_list", "ths_index_prices_snapshot", "a_share_valuations_snapshot",
                         "limit_pool_broken", "news_flash")),
    TierRule("quant.intraday_quote_observations", "quant.intraday_quote_observations_cold", "observed_at", None, 10, False,
             "sampled full quote evidence per watched stock"),
    TierRule("quant.intraday_rule_input_snapshots", "quant.intraday_rule_input_snapshots_cold", "observed_at", None, 10, False,
             "sampled rule-input snapshots per watched stock"),
)

# Research reads these from the hot tier; they are small and stay there.
KEEP_HOT = ("daily_bar", "legacy_daily_bar", "daily", "watch_daily_review", "capital_changes",
            "block_trade_supplement", "corporate_risk_supplement", "lhb_supplement", "moneyflow_supplement",
            "analyst_heat_supplement", "market_sentiment_close")

TRANSFER_WINDOW = {"timezone": "Asia/Shanghai", "trading_days": "15:45-08:45", "non_trading_days": "all day",
                   "never": "09:00-15:30 on a trading day"}
VERIFY = ("copy: INSERT INTO <cold_twin> SELECT ... ON CONFLICT DO NOTHING, bounded batches ordered by "
          "(<time_column>, primary key); delete from hot only rows whose primary key and payload hash "
          "exist in the cold twin and whose session is older than hot_sessions")


def tiering_policy() -> dict[str, Any]:
    return {"version": POLICY_VERSION, "rules": [asdict(rule) for rule in RULES], "keep_hot": list(KEEP_HOT),
            "transfer_window": TRANSFER_WINDOW, "verify_before_delete": VERIFY, "mover": "owner",
            "unlisted_capabilities": "stay hot until listed"}


def tiering_status(connection: Any) -> dict[str, Any]:
    """Policy plus tier usage and whether each cold twin exists (catalog reads only)."""
    usage = connection.execute(
        """SELECT coalesce(t.spcname,'pg_default') AS tablespace,
                  sum(pg_total_relation_size(c.oid))::bigint AS bytes
             FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
             LEFT JOIN pg_tablespace t ON t.oid=c.reltablespace
            WHERE n.nspname='quant' AND c.relkind IN ('r','m','p') GROUP BY 1""").fetchall()
    twins = sorted({rule.cold_twin for rule in RULES})
    present = {row["name"]: row for row in connection.execute(
        """SELECT n.nspname||'.'||c.relname AS name, coalesce(t.spcname,'pg_default') AS tablespace
             FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
             LEFT JOIN pg_tablespace t ON t.oid=c.reltablespace
            WHERE n.nspname||'.'||c.relname = ANY(%s)""", (twins,)).fetchall()}
    return {
        **tiering_policy(),
        "tier_usage_bytes": {row["tablespace"]: int(row["bytes"] or 0) for row in usage},
        "cold_twins": {name: {"exists": name in present,
                              "tablespace": (present.get(name) or {}).get("tablespace")} for name in twins},
    }


__all__ = ["KEEP_HOT", "POLICY_VERSION", "RULES", "TierRule", "tiering_policy", "tiering_status"]
