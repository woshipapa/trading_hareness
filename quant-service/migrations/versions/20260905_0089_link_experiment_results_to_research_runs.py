"""Link factor and strategy result rows to their reproducible research run."""

from alembic import op


revision = "20260905_0089"
down_revision = "20260905_0088"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # These columns remain nullable for legacy/event-research writers that
    # predate the reproducible-run ledger.  New native factor/backtest writes
    # always populate them with the run that produced the result.
    op.execute("""
        ALTER TABLE quant.factor_evaluations
            ADD COLUMN IF NOT EXISTS research_run_id uuid
            REFERENCES quant.research_experiment_runs(research_run_id)
            ON DELETE SET NULL
    """)
    op.execute("""
        ALTER TABLE quant.strategy_experiments
            ADD COLUMN IF NOT EXISTS research_run_id uuid
            REFERENCES quant.research_experiment_runs(research_run_id)
            ON DELETE SET NULL
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS factor_evaluations_research_run_idx
            ON quant.factor_evaluations(research_run_id)
            WHERE research_run_id IS NOT NULL
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS strategy_experiments_research_run_idx
            ON quant.strategy_experiments(research_run_id)
            WHERE research_run_id IS NOT NULL
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS quant.factor_evaluations_research_run_idx")
    op.execute("DROP INDEX IF EXISTS quant.strategy_experiments_research_run_idx")
    op.execute("ALTER TABLE quant.factor_evaluations DROP COLUMN IF EXISTS research_run_id")
    op.execute("ALTER TABLE quant.strategy_experiments DROP COLUMN IF EXISTS research_run_id")
