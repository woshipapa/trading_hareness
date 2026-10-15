"""Indexes for bounded intraday health and settlement reads.

Revision ID: 20261015_ow0122
Revises: 20261010_ow0121
"""

from alembic import op


revision = "20261015_ow0122"
down_revision = "20261010_ow0121"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # These are ordinary transactional indexes so the owner schema gate can
    # apply them atomically with the release. The owner should run the gate in
    # its maintenance window; no application code depends on a partial index.
    op.execute("""
        CREATE INDEX IF NOT EXISTS intraday_signal_events_observed_at_idx
            ON quant.intraday_signal_events(observed_at DESC)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS intraday_signal_outcomes_horizon_signal_idx
            ON quant.intraday_signal_outcomes(horizon_key,status,signal_event_id)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS daily_trade_limits_date_symbol_idx
            ON quant.daily_trade_limits(trading_date,symbol,available_at DESC)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS raw_market_provider_capability_available_idx
            ON quant.raw_market_observations(provider_key,capability,available_at DESC,symbol)
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS quant.intraday_signal_outcomes_horizon_signal_idx")
    op.execute("DROP INDEX IF EXISTS quant.intraday_signal_events_observed_at_idx")
    op.execute("DROP INDEX IF EXISTS quant.daily_trade_limits_date_symbol_idx")
    op.execute("DROP INDEX IF EXISTS quant.raw_market_provider_capability_available_idx")
