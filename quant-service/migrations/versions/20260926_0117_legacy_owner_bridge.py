"""Mark the pre-repository owner migration lineage as traversed.

The owner database records ``20260923_0117``, but the source migration was
not present in the owner release archives, Git objects, or available deploy
bundles. This marker preserves the observed version without recreating
unknown DDL. The following merge revision joins this lineage to the current
repository migration chain.
"""

from collections.abc import Sequence


revision: str = "20260923_0117"
down_revision: str | None = "20260906_0094"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Advance Alembic's version marker without applying unknown DDL."""


def downgrade() -> None:
    """Keep the bridge reversible as a version marker only."""
