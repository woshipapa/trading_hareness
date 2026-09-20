"""Materialize one bounded replay-readiness row per daily cross-section.

Revision ID: 20260919_ds0002
Revises: 20260918_ds0001
"""

from alembic import op


revision = "20260919_ds0002"
down_revision = "20260918_ds0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS quant.replay_readiness_daily_coverage (
            trading_date date PRIMARY KEY,
            expected_symbols integer NOT NULL CHECK (expected_symbols>=0),
            bar_symbols integer NOT NULL CHECK (bar_symbols>=0),
            fundamental_symbols integer NOT NULL CHECK (fundamental_symbols>=0),
            limit_symbols integer NOT NULL CHECK (limit_symbols>=0),
            is_full_cross_section boolean NOT NULL,
            coverage_definition text NOT NULL,
            refreshed_at timestamptz NOT NULL DEFAULT now()
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS replay_readiness_daily_full_date_idx
            ON quant.replay_readiness_daily_coverage(trading_date)
            WHERE is_full_cross_section
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS quant.replay_readiness_daily_coverage")
