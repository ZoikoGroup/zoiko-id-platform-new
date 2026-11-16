"""A record of Travel Rule messages (ZID-GAP-001 v2.0 DTV-33; CR-101).

Revision ID: verification_0017
Revises: verification_0016
Create Date: 2026-11-16

travel_rule_message: one row for each message a company records, in or out. It holds what was found, never the message: a SHA-256 of the canonical
payload (so the same message is recorded once and can be matched later), the two VASP identifiers (companies, not people), the state of each
counterparty, the threshold answer, the finding codes and the registry version. The people in the payload are not stored: how long a payload may be
kept, and where, is a Legal decision that comes with the partner contract. The row is append-only, and nothing here sends a message.
"""

from zid_platform.migrations import execute_script as _run

revision = "verification_0017"
down_revision = "verification_0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _run("""
        CREATE TABLE verify.travel_rule_message (
            message_id        uuid PRIMARY KEY,
            tenant_id         uuid NOT NULL,
            direction         text NOT NULL CHECK (direction IN ('OUTGOING', 'INCOMING')),
            jurisdiction      text NOT NULL CHECK (jurisdiction ~ '^[A-Z]{2}$'),
            payload_hash      text NOT NULL CHECK (payload_hash ~ '^[0-9a-f]{64}$'),
            originating_lei   text CHECK (originating_lei ~ '^[A-Z0-9]{20}$'),
            beneficiary_lei   text CHECK (beneficiary_lei ~ '^[A-Z0-9]{20}$'),
            well_formed       boolean NOT NULL,
            finding_codes     jsonb NOT NULL DEFAULT '[]'::jsonb,
            originating_vasp  text NOT NULL,
            beneficiary_vasp  text NOT NULL,
            threshold         text NOT NULL,
            registry_version  text NOT NULL,
            recorded_by       uuid NOT NULL,
            created_at        timestamptz NOT NULL DEFAULT now(),
            UNIQUE (tenant_id, direction, payload_hash)
        );
        CREATE INDEX travel_rule_message_recent ON verify.travel_rule_message (tenant_id, created_at DESC);

        CREATE FUNCTION verify.travel_rule_message_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'a recorded Travel Rule message never changes and is never deleted' USING ERRCODE = 'check_violation';
        END $$;
        CREATE TRIGGER travel_rule_message_append_only BEFORE UPDATE OR DELETE ON verify.travel_rule_message
            FOR EACH ROW EXECUTE FUNCTION verify.travel_rule_message_append_only();

        ALTER TABLE verify.travel_rule_message ENABLE ROW LEVEL SECURITY;
        ALTER TABLE verify.travel_rule_message FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant_isolation ON verify.travel_rule_message
            USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
            WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);
    """)


def downgrade() -> None:
    _run("""
        DROP TABLE verify.travel_rule_message;
        DROP FUNCTION verify.travel_rule_message_append_only();
    """)
