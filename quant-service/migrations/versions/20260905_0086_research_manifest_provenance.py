"""Persist manifest and code provenance for research data snapshots."""

from alembic import op


revision = "20260905_0086"
down_revision = "20260905_0085"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE quant.data_snapshots
            ADD COLUMN IF NOT EXISTS manifest_version text,
            ADD COLUMN IF NOT EXISTS code_sha text,
            ADD COLUMN IF NOT EXISTS data_schema_version text
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS data_snapshots_manifest_time_idx
            ON quant.data_snapshots(manifest_version, created_at DESC)
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS quant.data_snapshots_manifest_time_idx")
    op.execute("ALTER TABLE quant.data_snapshots DROP COLUMN IF EXISTS data_schema_version")
    op.execute("ALTER TABLE quant.data_snapshots DROP COLUMN IF EXISTS code_sha")
    op.execute("ALTER TABLE quant.data_snapshots DROP COLUMN IF EXISTS manifest_version")
