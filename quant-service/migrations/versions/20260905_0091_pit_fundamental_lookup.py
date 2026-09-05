"""Index daily fundamentals for point-in-time factor panel lookups.

Revision ID: 20260905_0091
Revises: 20260905_0090
"""

from alembic import op


revision = "20260905_0091"
down_revision = "20260905_0090"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS daily_fundamentals_pit_lookup_idx
          ON quant.daily_fundamentals(symbol,trading_date,available_at DESC)
          INCLUDE (total_mv,provider)
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS quant.daily_fundamentals_pit_lookup_idx")
