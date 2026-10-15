"""Indexes for high-frequency event and strategy read projections.

Revision ID: 20261016_ow0123
Revises: 20261015_ow0122
"""

from alembic import op


revision = "20261016_ow0123"
down_revision = "20261015_ow0122"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The event table is both an append-only evidence stream and the source for
    # close-pool research. Keep generic newest-first routes, event/source close
    # readers and symbol drill-downs on ordered indexes.
    op.execute("""
        CREATE INDEX IF NOT EXISTS market_events_occurred_created_idx
            ON quant.market_events(occurred_at DESC, created_at DESC)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS market_events_type_source_occurred_idx
            ON quant.market_events(event_type, source, occurred_at DESC, created_at DESC)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS market_events_symbol_occurred_idx
            ON quant.market_events(symbol, occurred_at DESC, created_at DESC)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS strategy_pattern_runs_latest_idx
            ON quant.strategy_pattern_runs(as_of_date DESC, updated_at DESC)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS strategy_pattern_samples_run_rank_idx
            ON quant.strategy_pattern_samples(run_id, rank)
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS quant.strategy_pattern_samples_run_rank_idx")
    op.execute("DROP INDEX IF EXISTS quant.strategy_pattern_runs_latest_idx")
    op.execute("DROP INDEX IF EXISTS quant.market_events_symbol_occurred_idx")
    op.execute("DROP INDEX IF EXISTS quant.market_events_type_source_occurred_idx")
    op.execute("DROP INDEX IF EXISTS quant.market_events_occurred_created_idx")
