"""Register the three declared strategies that had no promotion row.

``teacher_review_playbooks``, ``launch_radar`` and ``longhu_multifactor_shadow``
are declared in ``app/platform/strategy_registry.py`` but no revision seeded
them, so ``quant.strategy_promotion_registry`` did not match the declared
contracts.  They are registered disabled at zero weight like every other
contract; only an explicit promotion record may change that.

Revision ID: 20260926_srg0001
Revises: 20260926_trl0001
"""

from alembic import op


revision = "20260926_srg0001"
down_revision = "20260926_trl0001"
branch_labels = None
depends_on = None

STRATEGIES = (
    ("teacher_review_playbooks", "teacher-review-rules-v5"),
    ("launch_radar", "launch-radar-v1"),
    ("longhu_multifactor_shadow", "longhu-multifactor-shadow-v1"),
)


def upgrade() -> None:
    for key, version in STRATEGIES:
        op.execute(f"""
            INSERT INTO quant.strategy_promotion_registry(
                strategy_key,methodology_version,status,max_live_weight,reason,evidence)
            VALUES('{key}','{version}','disabled',0,
                   'P0 safety default: only an explicitly approved research version may supply a nonzero live weight.',
                   '{{"live_strategy_effect":"none"}}'::jsonb)
            ON CONFLICT(strategy_key) DO NOTHING
        """)


def downgrade() -> None:
    keys = ",".join(f"'{key}'" for key, _version in STRATEGIES)
    op.execute(f"DELETE FROM quant.strategy_promotion_registry WHERE strategy_key IN ({keys}) AND status='disabled'")
