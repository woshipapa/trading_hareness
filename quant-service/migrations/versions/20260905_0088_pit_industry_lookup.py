"""Index point-in-time industry lookups used by the factor panel."""

from alembic import op


revision = "20260905_0088"
down_revision = "20260905_0087"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The factor panel resolves one membership snapshot per symbol and date.
    # Put the lateral lookup keys first so a multi-year all-A evaluation does
    # not perform a membership-table scan for every daily bar.
    op.execute("""
        CREATE INDEX IF NOT EXISTS sector_membership_symbol_pit_lookup_idx
            ON quant.sector_membership_history(taxonomy_key,symbol,known_at DESC,effective_from DESC)
            INCLUDE (sector_key,effective_to)
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS quant.sector_membership_symbol_pit_lookup_idx")
