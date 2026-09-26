"""Join the observed owner lineage to the repository migration head."""

from collections.abc import Sequence


revision: str = "20260926_mrg0001"
down_revision: tuple[str, str] = ("20260923_0117", "20260926_srg0001")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Merge migration branches; child revisions perform the actual DDL."""


def downgrade() -> None:
    """Merge revision has no independent schema operation."""
