"""Record the availability cutoff used to build feature snapshots.

The existing ``as_of_date`` column identifies the exchange session.  It is not
enough to reproduce an intraday or delayed-data decision, so new snapshots also
retain the timezone-aware knowledge cutoff used by every availability-aware
reader.  Legacy rows remain nullable and are therefore visibly distinguishable
from snapshots built under this contract.
"""

from alembic import op


revision = "20260905_0085"
down_revision = "20260902_0084"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE quant.feature_snapshots
            ADD COLUMN IF NOT EXISTS knowledge_cutoff timestamptz
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS feature_snapshots_cutoff_idx
            ON quant.feature_snapshots(as_of_date, knowledge_cutoff DESC)
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS quant.feature_snapshots_cutoff_idx")
    op.execute("ALTER TABLE quant.feature_snapshots DROP COLUMN IF EXISTS knowledge_cutoff")
