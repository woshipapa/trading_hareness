"""Allow a standalone user reminder in the existing durable notification outbox."""

from alembic import op

revision = "20261009_0119"
down_revision = "20261008_0118"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        SET LOCAL lock_timeout = '3s';
        ALTER TABLE quant.intraday_advisory_deliveries
          DROP CONSTRAINT IF EXISTS intraday_advisory_deliveries_delivery_kind_check;
        ALTER TABLE quant.intraday_advisory_deliveries
          DROP CONSTRAINT IF EXISTS intraday_advisory_deliveries_check;
        DO $$ BEGIN
          IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='advisory_delivery_kind' AND conrelid='quant.intraday_advisory_deliveries'::regclass) THEN
            ALTER TABLE quant.intraday_advisory_deliveries ADD CONSTRAINT advisory_delivery_kind
              CHECK (delivery_kind IN ('signal','analysis','reminder'));
          END IF;
          IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='advisory_delivery_origin' AND conrelid='quant.intraday_advisory_deliveries'::regclass) THEN
            ALTER TABLE quant.intraday_advisory_deliveries ADD CONSTRAINT advisory_delivery_origin CHECK (
              (delivery_kind='reminder' AND event_id IS NULL AND analysis_run_id IS NULL) OR
              (delivery_kind IN ('signal','analysis') AND
                (event_id IS NOT NULL)::integer + (analysis_run_id IS NOT NULL)::integer = 1));
          END IF;
        END $$;
    """)


def downgrade() -> None:
    op.execute("""
        SET LOCAL lock_timeout = '3s';
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM quant.intraday_advisory_deliveries WHERE delivery_kind='reminder') THEN
            RAISE EXCEPTION 'personal reminder history must be archived before downgrade';
          END IF;
        END $$;
        ALTER TABLE quant.intraday_advisory_deliveries DROP CONSTRAINT IF EXISTS advisory_delivery_kind;
        ALTER TABLE quant.intraday_advisory_deliveries DROP CONSTRAINT IF EXISTS advisory_delivery_origin;
        DO $$ BEGIN
          IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='intraday_advisory_deliveries_delivery_kind_check' AND conrelid='quant.intraday_advisory_deliveries'::regclass) THEN
            ALTER TABLE quant.intraday_advisory_deliveries ADD CONSTRAINT intraday_advisory_deliveries_delivery_kind_check
              CHECK (delivery_kind IN ('signal','analysis'));
          END IF;
          IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='intraday_advisory_deliveries_check' AND conrelid='quant.intraday_advisory_deliveries'::regclass) THEN
            ALTER TABLE quant.intraday_advisory_deliveries ADD CONSTRAINT intraday_advisory_deliveries_check
              CHECK ((event_id IS NOT NULL)::integer + (analysis_run_id IS NOT NULL)::integer = 1);
          END IF;
        END $$;
    """)
