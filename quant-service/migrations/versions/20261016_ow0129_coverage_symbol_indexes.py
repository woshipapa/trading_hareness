"""Use narrow symbol indexes for the coverage cardinality projections.

Revision ID: 20261016_ow0129
Revises: 20261016_ow0128
"""

from alembic import op


revision = "20261016_ow0129"
down_revision = "20261016_ow0128"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The historical estimate reports the number of symbols represented by
    # each P0 dataset.  These narrow indexes avoid walking the wider
    # point-in-time lookup indexes when PostgreSQL computes count(DISTINCT
    # symbol); they do not change the point-in-time read path.
    op.execute("""
        CREATE INDEX IF NOT EXISTS daily_fundamentals_symbol_count_idx
            ON quant.daily_fundamentals(symbol)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS daily_trade_limits_symbol_count_idx
            ON quant.daily_trade_limits(symbol)
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS quant.daily_trade_limits_symbol_count_idx")
    op.execute("DROP INDEX IF EXISTS quant.daily_fundamentals_symbol_count_idx")
