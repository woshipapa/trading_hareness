"""A log of every research variant evaluated, for honest trial counts.

Revision ID: 20260926_trl0001
Revises: 20260926_t1s0001

quant.strategy_trials records approved lifecycle trials; it cannot answer how
many variants a result was selected from.  quant.research_trials does: one
row per evaluation of a variant over a sample window.  A family's trial count
is its number of distinct (variant_key, parameters_hash) pairs, which is what
the deflated Sharpe's null needs (app/research_trials.py).
"""

from alembic import op


revision = "20260926_trl0001"
down_revision = "20260926_t1s0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS quant.research_trials (
            trial_row_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            family text NOT NULL,
            variant_key text NOT NULL,
            parameters jsonb NOT NULL DEFAULT '{}'::jsonb,
            parameters_hash text NOT NULL,
            return_basis text NOT NULL,
            sample_start date,
            sample_end date,
            observations integer NOT NULL,
            mean_return numeric,
            sharpe numeric,
            probabilistic_sharpe numeric,
            deflated_sharpe numeric,
            family_trials integer NOT NULL,
            family_sharpe_variance numeric,
            expected_maximum_sharpe numeric,
            p_value numeric,
            q_value numeric,
            selection_gate text NOT NULL,
            source text NOT NULL,
            live_effect text NOT NULL DEFAULT 'none' CHECK (live_effect='none'),
            evaluated_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE(family, variant_key, parameters_hash, sample_start, sample_end)
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS research_trials_family_variant_idx
          ON quant.research_trials(family, variant_key, parameters_hash, evaluated_at DESC)
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS quant.research_trials")
