"""Speed up the bounded historical coverage projection.

Revision ID: 20261016_ow0128
Revises: 20261016_ow0127
"""

from alembic import op


revision = "20261016_ow0128"
down_revision = "20261016_ow0127"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # historical_estimate() groups the valid canonical rows by date and counts
    # distinct symbols.  The existing date-only index still makes PostgreSQL
    # sort millions of symbols before grouping; this partial covering index
    # lets the bounded research projection stream (date, symbol) directly.
    op.execute("""
        CREATE INDEX IF NOT EXISTS canonical_bars_daily_coverage_idx
            ON quant.canonical_bars_daily(trading_date, symbol)
            WHERE symbol <> '000300.SH' AND adj_factor > 0
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS quant.canonical_bars_daily_coverage_idx")
