"""Index the signals-by-scan projection used by the latest scan console read.

Revision ID: 20261016_ow0127
Revises: 20261016_ow0126
"""

from alembic import op


revision = "20261016_ow0127"
down_revision = "20261016_ow0126"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The latest-scan read filters by scan_id and presents the bounded rows in
    # severity/created order.  Keep the hot console path out of a full signal
    # table scan without changing the scan receipt or signal ordering rules.
    op.execute("""
        CREATE INDEX IF NOT EXISTS intraday_signal_events_scan_read_idx
            ON quant.intraday_signal_events(scan_id, severity DESC, created_at)
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS quant.intraday_signal_events_scan_read_idx")
