"""Index dated ST evidence by session for point-in-time reads.

Revision ID: 20260926_pit0001
Revises: 20260919_ds0005

Historical research now reads a session's ST status from the daily stock_st
cross-section instead of today's instrument flag.  Each read asks whether a
session is covered and whether one symbol is listed on it; neither is served
by the primary key (symbol first) or the observed_at index.

The id stays outside the numeric sequence for the same reason as
20260918_ds0001: the owner database records numbered revisions from a lineage
not in this repository.
"""

from alembic import op


revision = "20260926_pit0001"
down_revision = "20260919_ds0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE INDEX IF NOT EXISTS instrument_lifecycle_st_session_idx
          ON quant.instrument_lifecycle_evidence(status_date,symbol)
          WHERE list_status='UNKNOWN'
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS quant.instrument_lifecycle_st_session_idx")
