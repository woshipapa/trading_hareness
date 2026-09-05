"""Add covering indexes for the point-in-time replay readiness projection.

The readiness endpoint is a read-only control-plane check, but it touches the
large canonical daily tables.  These indexes keep the fresh-bar date scan and
the point-in-time control joins on narrow btree paths without changing any
research result or admitting data that fails the existing availability gates.
"""

from alembic import op


revision = "20260905_0092"
down_revision = "20260905_0091"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE INDEX IF NOT EXISTS canonical_bars_replay_fresh_date_idx
            ON quant.canonical_bars_daily(trading_date DESC, symbol)
            INCLUDE (available_at)
            WHERE quality_status='fresh'
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS daily_trade_limits_pit_lookup_idx
            ON quant.daily_trade_limits(symbol, trading_date, available_at DESC)
            INCLUDE (provider)
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS quant.daily_trade_limits_pit_lookup_idx")
    op.execute("DROP INDEX IF EXISTS quant.canonical_bars_replay_fresh_date_idx")
