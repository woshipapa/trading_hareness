"""Add durable raw overflow archive batches and offsets.

The tables are intentionally metadata-only: raw payloads are streamed to the
cloud worker in bounded batches and are never duplicated in PostgreSQL.
"""

from alembic import op


revision = "20260902_0084"
down_revision = "20260901_0083"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS quant.raw_archive_offsets (
            stream_key text PRIMARY KEY,
            capability text NOT NULL,
            effective_at timestamptz,
            observation_id uuid,
            updated_at timestamptz NOT NULL DEFAULT now(),
            state text NOT NULL DEFAULT 'idle'
                CHECK (state IN ('idle','cloud_overflow','critical','blocked')),
            last_error text,
            metadata jsonb NOT NULL DEFAULT '{}'::jsonb
        );
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS quant.raw_archive_batches (
            batch_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            stream_key text NOT NULL REFERENCES quant.raw_archive_offsets(stream_key) ON DELETE RESTRICT,
            capability text NOT NULL,
            first_effective_at timestamptz NOT NULL,
            first_observation_id uuid NOT NULL,
            last_effective_at timestamptz NOT NULL,
            last_observation_id uuid NOT NULL,
            row_count integer NOT NULL CHECK (row_count > 0),
            compressed_bytes integer NOT NULL CHECK (compressed_bytes > 0 AND compressed_bytes <= 268435456),
            sha256 text NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
            status text NOT NULL DEFAULT 'queued'
                CHECK (status IN ('queued','uploading','verified','retryable_failed','failed')),
            attempt_count integer NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
            available_at timestamptz NOT NULL DEFAULT now(),
            remote_path text,
            remote_fs_id text,
            last_error text,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE(stream_key, first_effective_at, first_observation_id, last_effective_at, last_observation_id, sha256)
        );
        CREATE INDEX IF NOT EXISTS raw_archive_batches_ready_idx
            ON quant.raw_archive_batches(status, available_at, created_at);
        CREATE INDEX IF NOT EXISTS raw_archive_batches_stream_idx
            ON quant.raw_archive_batches(stream_key, last_effective_at, last_observation_id);
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS quant.raw_archive_batches")
    op.execute("DROP TABLE IF EXISTS quant.raw_archive_offsets")
