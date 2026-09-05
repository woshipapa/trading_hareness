"""Compatibility marker for the owner database's legacy 0089 revision.

The owner database was stamped by the peer release line with this revision
identifier.  The current release renamed the same sequence to a dated
revision, so Alembic needs a no-op marker to traverse the existing database
without rewriting or dropping any data.  The merge revision after the current
head joins this compatibility branch back to the current migration graph.
"""

revision = "20260902_0089"
down_revision = "20260901_0083"
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
