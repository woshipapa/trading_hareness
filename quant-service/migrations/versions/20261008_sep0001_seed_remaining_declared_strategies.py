"""Register the three declared strategies that still had no promotion row.

``multi_factor_rank_v1``, ``board_flow_drill`` and ``dragon_leader_research``
are declared in ``app/platform/strategy_registry.py`` but no revision seeded
them, so ``quant.strategy_promotion_registry`` did not match the declared
contracts and ``test_every_declared_strategy_is_seeded_disabled_and_zero_weight``
failed against the working tree.

This is the ``owner-schema`` half of a change whose ``owner-quant`` half already
shipped: the code declares the contracts, the schema has to carry their rows.
Exactly the lineage AGENTS.md means by "a database migration must be applied and
verified before switching code that depends on its schema".

Registered disabled at zero weight, like every other contract and like the
precedent in ``20260926_srg0001``: registration is not execution permission,
and only an explicit promotion record may supply a nonzero live weight.

Revision ID: 20261008_sep0001
Revises: 20260926_mrg0001
"""

from alembic import op


revision = "20261008_sep0001"
down_revision = "20260926_mrg0001"
branch_labels = None
depends_on = None

#: ``(strategy_key, methodology_version)`` exactly as declared in the registry.
STRATEGIES = (
    ("multi_factor_rank_v1", "strategy-v1"),
    ("board_flow_drill", "board-flow-drill-v1"),
    ("dragon_leader_research", "dragon-leader-v1"),
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
    for key, _version in STRATEGIES:
        op.execute(f"DELETE FROM quant.strategy_promotion_registry WHERE strategy_key='{key}'")
