"""Index the source-leading quote projection used by the runtime status board.

Revision ID: 20261016_ow0125
Revises: 20261016_ow0124
"""

from alembic import op


revision = "20261016_ow0125"
down_revision = "20261016_ow0124"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The status projection filters by source and aggregates each source.  The
    # older symbol-leading uniqueness index forces a broad index walk; this
    # source-leading covering path keeps the read bounded to the four status
    # sources without changing quote persistence or retention.
    op.execute("""
        CREATE INDEX IF NOT EXISTS intraday_quote_status_source_observed_idx
            ON quant.intraday_quote_observations(source_name, observed_at DESC)
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS quant.intraday_quote_status_source_observed_idx")
