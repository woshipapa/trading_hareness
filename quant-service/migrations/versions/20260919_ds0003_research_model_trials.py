"""Add append-only trial evidence for offline research models.

Revision ID: 20260919_ds0003
Revises: 20260919_ds0002
"""

from alembic import op


revision = "20260919_ds0003"
down_revision = "20260919_ds0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS quant.research_model_trials (
            trial_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            model_id uuid NOT NULL REFERENCES quant.research_model_registry(model_id) ON DELETE RESTRICT,
            trial_key text NOT NULL,
            training_version text NOT NULL,
            status text NOT NULL CHECK(status IN ('completed','blocked','failed')),
            parameters jsonb NOT NULL DEFAULT '{}'::jsonb,
            metrics jsonb NOT NULL DEFAULT '{}'::jsonb,
            independent_days integer NOT NULL DEFAULT 0 CHECK(independent_days>=0),
            sample_count integer NOT NULL DEFAULT 0 CHECK(sample_count>=0),
            artifact_sha256 text NOT NULL CHECK(artifact_sha256 ~ '^[0-9a-f]{64}$'),
            metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
            live_effect text NOT NULL DEFAULT 'none' CHECK(live_effect='none'),
            created_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE(model_id,trial_key)
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS research_model_trials_model_time_idx
            ON quant.research_model_trials(model_id,created_at DESC)
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS quant.research_model_trials")
