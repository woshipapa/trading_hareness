"""Separate preview evidence from confirmed discipline evaluations.

Revision ID: 20261008_0118
Revises: 20261008_sep0002 (already applied external production revision)

Bounded by card lines and sessions: at most three events per line/session, not
an append-only quote store. Existing formal evaluation/outbox tables are intact.
"""
from alembic import op

revision = "20261008_0118"
down_revision = "20261010_ow0120"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS quant.discipline_card_observation_states (
            plan_id uuid NOT NULL REFERENCES quant.discipline_plans(plan_id) ON DELETE CASCADE,
            line_key text NOT NULL CHECK (line_key LIKE 'preview-v1:%'),
            session_date date NOT NULL,
            state text NOT NULL CHECK (state IN
                ('waiting','price_ready','price_blocked','unavailable','inactive')),
            consumed jsonb NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(consumed)='array'),
            payload jsonb NOT NULL,
            first_observed_at timestamptz NOT NULL,
            last_observed_at timestamptz NOT NULL,
            PRIMARY KEY (plan_id,line_key,session_date)
        )
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS quant.discipline_card_observation_events (
            event_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            event_key text NOT NULL UNIQUE,
            plan_id uuid NOT NULL REFERENCES quant.discipline_plans(plan_id) ON DELETE CASCADE,
            line_key text NOT NULL,
            session_date date NOT NULL,
            event_kind text NOT NULL CHECK (event_kind IN ('price_ready','price_blocked','withdrawn')),
            observed_at timestamptz NOT NULL,
            payload jsonb NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE (plan_id,line_key,session_date,event_kind),
            FOREIGN KEY (plan_id,line_key,session_date)
                REFERENCES quant.discipline_card_observation_states(plan_id,line_key,session_date)
                ON DELETE CASCADE
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS discipline_card_observation_events_time_idx
            ON quant.discipline_card_observation_events (observed_at DESC)
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS quant.discipline_card_observation_deliveries (
            delivery_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            event_id uuid NOT NULL UNIQUE
                REFERENCES quant.discipline_card_observation_events(event_id) ON DELETE CASCADE,
            status text NOT NULL CHECK (status IN ('pending','sent','failed','disabled','cancelled')),
            message_text text NOT NULL,
            message_card jsonb NOT NULL,
            attempt_count integer NOT NULL DEFAULT 0 CHECK (attempt_count>=0),
            next_attempt_at timestamptz,
            response jsonb NOT NULL DEFAULT '{}'::jsonb,
            error_message text,
            sent_at timestamptz,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now()
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS discipline_card_observation_deliveries_due_idx
            ON quant.discipline_card_observation_deliveries (next_attempt_at,created_at)
            WHERE status IN ('pending','failed')
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS quant.discipline_card_observation_deliveries")
    op.execute("DROP TABLE IF EXISTS quant.discipline_card_observation_events")
    op.execute("DROP TABLE IF EXISTS quant.discipline_card_observation_states")
