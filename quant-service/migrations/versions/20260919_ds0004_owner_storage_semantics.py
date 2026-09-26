"""Record owner storage/adjustment semantics without changing live effects.

Revision ID: 20260919_ds0004
Revises: 20260919_ds0003

The Windows owner release may install these columns before the peer image is
published. Every operation is idempotent so a local research database can
adopt the same contract. The read-only peer can still report a pre-cutover
catalog, but the strategy/research release must not be switched on until the
owner diagnostics prove these columns and their zero-violation data guard.
"""

from alembic import op


revision = "20260919_ds0004"
down_revision = "20260919_ds0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Release 1 may create the *_cold twins before Release 2 applies this
    # semantic migration.  Apply the same ordered columns and constraints to
    # every relation that already exists; otherwise owner_storage's signature
    # check would permanently reject an otherwise complete cutover.
    for table in ("market_bars_daily", "canonical_bars_daily"):
        provider_column = "source" if table == "market_bars_daily" else "selected_provider"
        for relation in (table, f"{table}_cold"):
            op.execute(f"""
                DO $$
                BEGIN
                    IF to_regclass('quant.{relation}') IS NOT NULL THEN
                        ALTER TABLE quant.{relation}
                            ADD COLUMN IF NOT EXISTS adjustment_state text NOT NULL DEFAULT 'absent';
                        UPDATE quant.{relation} SET adjustment_state='absent' WHERE adjustment_state IS NULL;
                        ALTER TABLE quant.{relation}
                            DROP CONSTRAINT IF EXISTS {relation}_adjustment_state_check;
                        ALTER TABLE quant.{relation}
                            ADD CONSTRAINT {relation}_adjustment_state_check
                            CHECK (adjustment_state IN ('complete','pending','retired','absent'));
                        -- Recompute every legacy bar row. Older schemas may
                        -- have supplied a default ``complete`` before the
                        -- provider contract existed; restricting this update
                        -- to ``absent`` would preserve non-Tushare rows as
                        -- research-eligible.
                        UPDATE quant.{relation}
                           SET adjustment_state=CASE
                               WHEN adj_factor IS NULL THEN 'absent'
                               WHEN {provider_column} IN ('tushare','tushare_primary','tushare_super_get',
                                                          'tushare_super_sdk','tushare_super','tushare_backup',
                                                          'longhu_qfq_derived')
                                    AND adj_factor > 0 THEN 'complete'
                               ELSE 'pending'
                           END
                         WHERE adjustment_state IS DISTINCT FROM CASE
                               WHEN adj_factor IS NULL THEN 'absent'
                               WHEN {provider_column} IN ('tushare','tushare_primary','tushare_super_get',
                                                          'tushare_super_sdk','tushare_super','tushare_backup',
                                                          'longhu_qfq_derived')
                                    AND adj_factor > 0 THEN 'complete'
                               ELSE 'pending'
                           END;
                        ALTER TABLE quant.{relation} ALTER COLUMN adjustment_state SET NOT NULL;
                        -- Keep the read-only readiness probe cheap on a clean
                        -- multi-million-row table.  The partial index is
                        -- empty when the contract is clean and points only
                        -- at rows the guard must report.
                        CREATE INDEX IF NOT EXISTS {relation}_adjustment_invalid_idx
                            ON quant.{relation} ((1))
                         WHERE adjustment_state='complete' AND (adj_factor IS NULL OR adj_factor<=0);
                    END IF;
                END $$;
            """)

    for relation in ("daily_adjustment_factors", "daily_adjustment_factors_cold"):
        op.execute(f"""
            DO $$
            BEGIN
                IF to_regclass('quant.{relation}') IS NOT NULL THEN
                    ALTER TABLE quant.{relation} ALTER COLUMN adj_factor DROP NOT NULL;
                    ALTER TABLE quant.{relation}
                        ADD COLUMN IF NOT EXISTS adjustment_state text NOT NULL DEFAULT 'complete',
                        ADD COLUMN IF NOT EXISTS factor_semantics text NOT NULL DEFAULT 'cumulative_tushare',
                        ADD COLUMN IF NOT EXISTS retired_at timestamptz;
                    UPDATE quant.{relation} SET adjustment_state='absent' WHERE adjustment_state IS NULL;
                    UPDATE quant.{relation} SET factor_semantics='unknown' WHERE factor_semantics IS NULL;
                    ALTER TABLE quant.{relation}
                        DROP CONSTRAINT IF EXISTS {relation}_state_check;
                    ALTER TABLE quant.{relation}
                        ADD CONSTRAINT {relation}_state_check
                        CHECK (adjustment_state IN ('complete','pending','retired','absent'));
                    ALTER TABLE quant.{relation}
                        DROP CONSTRAINT IF EXISTS {relation}_semantics_check;
                    ALTER TABLE quant.{relation}
                        ADD CONSTRAINT {relation}_semantics_check
                        CHECK (factor_semantics IN ('cumulative_tushare','same_day_identity_only','unknown'));
                    -- Existing placeholders are retained as audit evidence but
                    -- cannot satisfy research-price joins. Normalize the
                    -- semantic label first, then derive state from that label
                    -- and the actual factor value; a legacy default must not
                    -- turn an unknown row into ``complete``.
                    UPDATE quant.{relation}
                       SET factor_semantics=CASE
                               WHEN raw->>'factor_semantics'='same_day_identity_only'
                                   THEN 'same_day_identity_only'
                               WHEN provider IN ('tushare','tushare_primary','tushare_super_get',
                                                 'tushare_super_sdk','tushare_super','tushare_backup',
                                                 'longhu_qfq_derived')
                                   AND COALESCE(raw->>'factor_semantics','cumulative_tushare')='cumulative_tushare'
                                   THEN 'cumulative_tushare'
                               ELSE 'unknown'
                           END
                     WHERE factor_semantics IS NULL
                        OR factor_semantics='cumulative_tushare'
                        OR factor_semantics NOT IN ('same_day_identity_only','unknown');
                    UPDATE quant.{relation}
                       SET adjustment_state=CASE
                               WHEN factor_semantics IN ('same_day_identity_only','unknown') THEN 'retired'
                               WHEN adj_factor IS NULL OR adj_factor <= 0 THEN 'pending'
                               ELSE 'complete'
                           END,
                           retired_at=CASE
                               WHEN factor_semantics IN ('same_day_identity_only','unknown')
                                   THEN COALESCE(retired_at,now())
                               ELSE retired_at
                           END
                     WHERE adjustment_state='complete';
                    ALTER TABLE quant.{relation}
                        ALTER COLUMN adjustment_state SET NOT NULL,
                        ALTER COLUMN factor_semantics SET NOT NULL;
                    CREATE INDEX IF NOT EXISTS {relation}_adjustment_state_idx
                        ON quant.{relation}(trading_date DESC)
                     WHERE adjustment_state='complete';
                    CREATE INDEX IF NOT EXISTS {relation}_research_state_idx
                        ON quant.{relation}(symbol,trading_date,available_at DESC)
                     WHERE adjustment_state='complete' AND factor_semantics='cumulative_tushare';
                    CREATE INDEX IF NOT EXISTS {relation}_semantic_invalid_idx
                        ON quant.{relation} ((1))
                     WHERE adjustment_state='complete'
                       AND (factor_semantics IS DISTINCT FROM 'cumulative_tushare'
                            OR adj_factor IS NULL OR adj_factor<=0
                            OR provider IS NULL
                            OR provider NOT IN ('tushare','tushare_primary','tushare_super_get',
                                                'tushare_super_sdk','tushare_super','tushare_backup',
                                                'longhu_qfq_derived'));
                END IF;
            END $$;
        """)


def downgrade() -> None:
    for table in ("market_bars_daily", "canonical_bars_daily"):
        for relation in (table, f"{table}_cold"):
            op.execute(f"""
                DO $$
                BEGIN
                    IF to_regclass('quant.{relation}') IS NOT NULL THEN
                        ALTER TABLE quant.{relation} DROP CONSTRAINT IF EXISTS {relation}_adjustment_state_check;
                        ALTER TABLE quant.{relation} DROP COLUMN IF EXISTS adjustment_state;
                    END IF;
                END $$;
            """)
    for relation in ("daily_adjustment_factors", "daily_adjustment_factors_cold"):
        op.execute(f"""
            DROP INDEX IF EXISTS quant.{relation}_research_state_idx;
            DO $$
            BEGIN
                IF to_regclass('quant.{relation}') IS NOT NULL THEN
                    ALTER TABLE quant.{relation} DROP CONSTRAINT IF EXISTS {relation}_state_check;
                    ALTER TABLE quant.{relation} DROP CONSTRAINT IF EXISTS {relation}_semantics_check;
                    ALTER TABLE quant.{relation} DROP COLUMN IF EXISTS retired_at;
                    ALTER TABLE quant.{relation} DROP COLUMN IF EXISTS factor_semantics;
                    ALTER TABLE quant.{relation} DROP COLUMN IF EXISTS adjustment_state;
                END IF;
            END $$;
        """)
