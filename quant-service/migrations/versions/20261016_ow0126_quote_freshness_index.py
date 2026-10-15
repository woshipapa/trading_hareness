"""Index global quote freshness checks used by strategy health.

Revision ID: 20261016_ow0126
Revises: 20261016_ow0125
"""

from alembic import op


revision = "20261016_ow0126"
down_revision = "20261016_ow0125"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Strategy health asks for max(observed_at) and a recent-row count across
    # all quote sources.  A global time-leading index makes both predicates
    # index-only instead of repeatedly scanning the source-leading status
    # index or the full observation heap.
    op.execute("""
        CREATE INDEX IF NOT EXISTS intraday_quote_observed_at_idx
            ON quant.intraday_quote_observations(observed_at DESC)
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS quant.intraday_quote_observed_at_idx")
