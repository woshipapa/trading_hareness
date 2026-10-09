"""Validate required logical definitions only; shared 0119 receives no DDL."""
from alembic import op
from migrations.owner_legacy_reconcile import validate

revision='20261010_ow0121'
down_revision='20261009_0119'
branch_labels=None
depends_on=None

def upgrade():
    bind=op.get_bind()
    bind.exec_driver_sql("SET LOCAL statement_timeout='30s'")
    validate(bind,final=True)

def downgrade():
    raise RuntimeError('owner alignment validation is forward-only')
