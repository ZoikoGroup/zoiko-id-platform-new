"""The staged rollout of a market-matrix row, and its pause and rollback (ZID-COM-WF-2026-11 WF-I05 rollout panel).

Revision ID: commercial_0031
Revises: commercial_0030
Create Date: 2026-11-13

A row of the ACTIVE matrix is only sold once it has been taken through SANDBOX, STAGING, PILOT, CANARY and PRODUCTION, one stage at a time, each move by a different
person than the last. Only CANARY and PRODUCTION count as executed release evidence. A market can be PAUSED (and resumed to where it was) or ROLLED_BACK at any time.
The log is append-only: the current stage is the latest entry, and nothing is ever edited or deleted, so what was true on any day can be shown.
"""

from zid_platform.migrations import execute_script as _run

revision = "commercial_0031"
down_revision = "commercial_0030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _run("""
        CREATE TABLE commercial.market_rollout (
            seq         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            family      text NOT NULL CHECK (family ~ '^[A-Z]{2,8}$'),
            country     text NOT NULL CHECK (country ~ '^[A-Z]{2}$'),
            stage       text NOT NULL CHECK (stage IN ('SANDBOX', 'STAGING', 'PILOT', 'CANARY', 'PRODUCTION', 'PAUSED', 'ROLLED_BACK')),
            from_stage  text CHECK (from_stage IN ('SANDBOX', 'STAGING', 'PILOT', 'CANARY', 'PRODUCTION')),
            decided_by  uuid NOT NULL,
            reason      text NOT NULL CHECK (char_length(btrim(reason)) BETWEEN 3 AND 500),
            decided_at  timestamptz NOT NULL DEFAULT now(),
            CHECK ((stage = 'PAUSED') = (from_stage IS NOT NULL))
        );
        CREATE INDEX market_rollout_key ON commercial.market_rollout (family, country, seq DESC);
        CREATE FUNCTION commercial.market_rollout_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'the rollout log is append-only' USING ERRCODE = 'check_violation';
        END $$;
        CREATE TRIGGER market_rollout_guard BEFORE UPDATE OR DELETE ON commercial.market_rollout
            FOR EACH ROW EXECUTE FUNCTION commercial.market_rollout_guard();
    """)


def downgrade() -> None:
    _run("""
        DROP TABLE commercial.market_rollout;
        DROP FUNCTION commercial.market_rollout_guard();
    """)
