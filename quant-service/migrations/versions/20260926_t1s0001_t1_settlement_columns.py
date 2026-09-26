"""Record how each daily outcome was settled under the T+1 rule.

Revision ID: 20260926_t1s0001
Revises: 20260926_pit0001

The four daily outcome tables are now written by one settlement rule
(app/t1_settlement.py): exit no earlier than the session after entry, rolled
past a limit-down or suspended exit, adjusted prices, net of costs, against an
equal-weight A-share benchmark.  These columns carry what that rule decided,
so a reader can tell a rolled exit or an unadjusted window from an ordinary
one, and ``settlement_version`` lets a settle run skip rows already current.
Existing columns keep their names; ``benchmark_return`` is described by
``benchmark_key`` (NULL on rows settled before this revision: CSI 300).
"""

from alembic import op


revision = "20260926_t1s0001"
down_revision = "20260926_pit0001"
branch_labels = None
depends_on = None

TABLES = (
    "outcomes", "post_close_strategy_candidate_outcomes", "ten_day_leader_rotation_candidate_outcomes",
    "strategy_daily_candidate_outcomes",
)
COLUMNS = (
    ("exit_date", "date"), ("net_return", "numeric"), ("benchmark_key", "text"), ("sessions_held", "integer"),
    ("exit_rolled_sessions", "integer"), ("price_basis", "text"), ("settlement_version", "text"),
)


def upgrade() -> None:
    for table in TABLES:
        for column, kind in COLUMNS:
            op.execute(f"ALTER TABLE quant.{table} ADD COLUMN IF NOT EXISTS {column} {kind}")
        if table != "outcomes":
            # quant.outcomes already has a direction; the candidate lines were
            # long-only by assumption and now record it.
            op.execute(f"ALTER TABLE quant.{table} ADD COLUMN IF NOT EXISTS direction smallint NOT NULL DEFAULT 1")
        op.execute(f"CREATE INDEX IF NOT EXISTS {table}_settlement_version_idx ON quant.{table}(settlement_version)")


def downgrade() -> None:
    for table in TABLES:
        op.execute(f"DROP INDEX IF EXISTS quant.{table}_settlement_version_idx")
        for column, _kind in COLUMNS:
            op.execute(f"ALTER TABLE quant.{table} DROP COLUMN IF EXISTS {column}")
        if table != "outcomes":
            op.execute(f"ALTER TABLE quant.{table} DROP COLUMN IF EXISTS direction")
