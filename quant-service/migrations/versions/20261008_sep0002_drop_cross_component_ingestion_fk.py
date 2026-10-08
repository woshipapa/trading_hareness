"""Drop the only hard constraint that crossed a component boundary.

``quant.analyst_signals.ingestion_job_id`` referenced
``public.ingestion_jobs(job_id)`` **ON DELETE CASCADE**. ``ingestion_jobs`` is
the Feishu relay's delivery ledger (``feishu-relay/adapter/ledger.mjs`` owns
it), so deleting one relay delivery row silently deleted quant research
signals — a write on one component's data destroying another's.

It was also the one thing forcing a quant-only database to fake a foreign
table: ``standalone/001-ingestion-contract.sql`` creates a stub
``public.ingestion_jobs`` purely so this constraint can be created.

The column stays, ``NOT NULL`` and unchanged: every signal really did arrive
through some ingestion job, and research evidence must keep that provenance.
What goes away is the *enforced* cross-component reference and its cascade.
After this, the two components share a database by deployment convenience
only, not by schema contract.

Reversible: ``downgrade`` re-creates the constraint exactly as it was. It will
fail if any orphan rows accumulated while the constraint was absent, which is
the correct, loud outcome rather than a silent partial restore.

Revision ID: 20261008_sep0002
Revises: 20261008_sep0001
"""

from alembic import op


revision = "20261008_sep0002"
down_revision = "20261008_sep0001"
branch_labels = None
depends_on = None

CONSTRAINT = "analyst_signals_ingestion_job_id_fkey"


def upgrade() -> None:
    op.execute(f"ALTER TABLE quant.analyst_signals DROP CONSTRAINT IF EXISTS {CONSTRAINT}")


def downgrade() -> None:
    op.execute(f"""
        ALTER TABLE quant.analyst_signals
          ADD CONSTRAINT {CONSTRAINT}
          FOREIGN KEY (ingestion_job_id) REFERENCES public.ingestion_jobs(job_id) ON DELETE CASCADE
    """)
