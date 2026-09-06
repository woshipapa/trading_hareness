"""Add an append-only registry for offline research model artifacts."""

from alembic import op


revision = "20260906_0094"
down_revision = "20260905_0093"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS quant.research_model_registry (
            model_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            model_key text NOT NULL,
            model_family text NOT NULL,
            model_version text NOT NULL,
            framework text NOT NULL,
            artifact_uri text,
            artifact_sha256 text CHECK (artifact_sha256 IS NULL OR artifact_sha256 ~ '^[0-9a-f]{64}$'),
            data_snapshot_key text,
            feature_contract_version text NOT NULL,
            label_contract_version text NOT NULL,
            status text NOT NULL CHECK (status IN ('draft','trained','validated','rejected','advisory_champion','retired')),
            trial_count integer NOT NULL DEFAULT 0 CHECK (trial_count >= 0),
            independent_days integer NOT NULL DEFAULT 0 CHECK (independent_days >= 0),
            sample_count integer NOT NULL DEFAULT 0 CHECK (sample_count >= 0),
            metrics jsonb NOT NULL DEFAULT '{}'::jsonb,
            metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
            approved_by text,
            approved_at timestamptz,
            created_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE(model_key, model_version)
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS research_model_registry_status_idx
            ON quant.research_model_registry(status,created_at DESC)
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS quant.research_model_registry_status_idx")
    op.execute("DROP TABLE IF EXISTS quant.research_model_registry")
