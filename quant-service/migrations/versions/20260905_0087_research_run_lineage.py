"""Add an append-only ledger for reproducible research runs and lineage."""

from alembic import op


revision = "20260905_0087"
down_revision = "20260905_0086"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS quant.research_experiment_runs (
            research_run_id uuid PRIMARY KEY,
            experiment_type text NOT NULL CHECK (experiment_type IN (
                'snapshot_build','factor_evaluation','strategy_backtest','intraday_replay','event_study'
            )),
            strategy_key text,
            strategy_version text,
            universe_key text,
            start_date date,
            end_date date,
            knowledge_cutoff timestamptz NOT NULL,
            data_manifest_id text,
            code_sha text NOT NULL DEFAULT 'unknown',
            data_schema_version text NOT NULL,
            parameters jsonb NOT NULL DEFAULT '{}'::jsonb,
            status text NOT NULL CHECK (status IN ('running','completed','blocked','failed')),
            output_digest text CHECK (output_digest IS NULL OR output_digest ~ '^[0-9a-f]{64}$'),
            error_message text,
            started_at timestamptz NOT NULL DEFAULT now(),
            finished_at timestamptz,
            created_at timestamptz NOT NULL DEFAULT now()
        )
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS quant.research_lineage_edges (
            lineage_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            research_run_id uuid NOT NULL REFERENCES quant.research_experiment_runs(research_run_id) ON DELETE CASCADE,
            direction text NOT NULL CHECK (direction IN ('input','output')),
            dataset_key text NOT NULL,
            dataset_version text NOT NULL,
            content_sha256 text NOT NULL CHECK (content_sha256 ~ '^[0-9a-f]{64}$'),
            metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
            created_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE(research_run_id,direction,dataset_key,dataset_version,content_sha256)
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS research_runs_latest_idx
            ON quant.research_experiment_runs(experiment_type,created_at DESC)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS research_lineage_run_idx
            ON quant.research_lineage_edges(research_run_id,direction,created_at)
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS quant.research_lineage_edges")
    op.execute("DROP TABLE IF EXISTS quant.research_experiment_runs")
