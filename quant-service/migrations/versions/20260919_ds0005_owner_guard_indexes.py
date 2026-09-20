"""Add bounded indexes for owner semantic data guards.

Revision ID: 20260919_ds0005
Revises: 20260919_ds0004

Some owner databases were stamped at the semantic revision after the columns
were installed but did not receive the empty-on-clean invalid-row indexes.
Without these indexes, the read-only health guard can seq-scan millions of
bars on every startup/readiness request.  This revision is idempotent and
applies to hot and already-created cold twins independently.
"""

from alembic import op


revision = "20260919_ds0005"
down_revision = "20260919_ds0004"
branch_labels = None
depends_on = None


_COMPLETE_FACTOR_PROVIDERS = (
    "'tushare','tushare_primary','tushare_super_get',"
    "'tushare_super_sdk','tushare_super','tushare_backup',"
    "'longhu_qfq_derived'"
)


def upgrade() -> None:
    for relation in (
        "canonical_bars_daily", "canonical_bars_daily_cold",
        "market_bars_daily", "market_bars_daily_cold",
    ):
        op.execute(f"""
            DO $$
            BEGIN
                IF to_regclass('quant.{relation}') IS NOT NULL THEN
                    CREATE INDEX IF NOT EXISTS {relation}_semantic_guard_idx
                        ON quant.{relation} (symbol, trading_date)
                     WHERE adjustment_state='complete'
                       AND (adj_factor IS NULL OR adj_factor<=0);
                END IF;
            END $$;
        """)

    for relation in ("daily_adjustment_factors", "daily_adjustment_factors_cold"):
        op.execute(f"""
            DO $$
            BEGIN
                IF to_regclass('quant.{relation}') IS NOT NULL THEN
                    CREATE INDEX IF NOT EXISTS {relation}_semantic_guard_idx
                        ON quant.{relation} (symbol, trading_date)
                     WHERE adjustment_state='complete'
                       AND (factor_semantics IS DISTINCT FROM 'cumulative_tushare'
                            OR adj_factor IS NULL OR adj_factor<=0
                            OR provider IS NULL
                            OR provider NOT IN ({_COMPLETE_FACTOR_PROVIDERS}));
                END IF;
            END $$;
        """)


def downgrade() -> None:
    for relation in (
        "canonical_bars_daily", "canonical_bars_daily_cold",
        "market_bars_daily", "market_bars_daily_cold",
        "daily_adjustment_factors", "daily_adjustment_factors_cold",
    ):
        op.execute(f"DROP INDEX IF EXISTS quant.{relation}_semantic_guard_idx")
