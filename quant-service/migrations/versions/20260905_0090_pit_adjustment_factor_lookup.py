"""Index point-in-time corporate-action factor lookups for factor research."""

from alembic import op


revision = "20260905_0090"
down_revision = "20260905_0089"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE INDEX IF NOT EXISTS daily_adjustment_factors_pit_lookup_idx
            ON quant.daily_adjustment_factors(symbol,trading_date,available_at DESC)
            INCLUDE (adj_factor,provider)
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS quant.daily_adjustment_factors_pit_lookup_idx")
