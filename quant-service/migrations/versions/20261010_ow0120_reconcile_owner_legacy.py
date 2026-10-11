"""Add observed owner prerequisites without rewriting existing migrations."""
from alembic import op
from migrations.owner_legacy_reconcile import reconcile

revision='20261010_ow0120'
down_revision='20261008_sep0002'
branch_labels=None
depends_on=None

def upgrade():
    reconcile(op)

def downgrade():
    raise RuntimeError('owner legacy reconciliation is forward-only')
