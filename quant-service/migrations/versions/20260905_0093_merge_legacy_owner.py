"""Merge the legacy owner revision branch into the current schema head."""

from alembic import op


revision = "20260905_0093"
down_revision = ("20260902_0089", "20260905_0092")
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The branch merge is intentionally schema-neutral.  Missing objects are
    # created by the current 0084+ revisions while preserving owner data.
    pass


def downgrade() -> None:
    pass
