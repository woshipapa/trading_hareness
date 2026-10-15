"""Indexes for the runtime rule-input provenance counters.

Revision ID: 20261016_ow0124
Revises: 20261016_ow0123
"""

from alembic import op


revision = "20261016_ow0124"
down_revision = "20261016_ow0123"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The status board counts the two retained input schemas on every refresh.
    # Partial indexes avoid reparsing the large inputs JSONB payload for those
    # exact counts while preserving the all-row total and latest timestamp.
    op.execute("""
        CREATE INDEX IF NOT EXISTS intraday_rule_input_snapshot_v2_idx
            ON quant.intraday_rule_input_snapshots(rule_input_snapshot_id)
         WHERE inputs->>'schema_version'='intraday-rule-input-v2'
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS intraday_rule_input_snapshot_v1_idx
            ON quant.intraday_rule_input_snapshots(rule_input_snapshot_id)
         WHERE inputs->>'schema_version'='intraday-rule-input-v1'
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS quant.intraday_rule_input_snapshot_v1_idx")
    op.execute("DROP INDEX IF EXISTS quant.intraday_rule_input_snapshot_v2_idx")
