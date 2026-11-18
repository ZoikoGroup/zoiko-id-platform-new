"""WF-05 contact claims and one-time challenges (ZID-DTV-WF-05 DTV-20, DTV-21, §06 ContactClaim and Challenge; CR-114).

Revision ID: verification_0023
Revises: verification_0022
Create Date: 2026-11-18

check_contact: an email address or phone number a case asked to check. The value itself is encrypted in the evidence vault (vault_object_id); here only
a tenant-scoped keyed hash, the masked form and the revision. A changed contact is a new row with the next revision; the earlier one is superseded.

check_challenge: one challenge to one contact revision. The code is kept only as a keyed hash. A final state (VERIFIED, EXPIRED, REVOKED) never
changes; the binding (contact, revision, channel, session) never changes; rows are never deleted. Both tables are tenant data under row-level security.
"""

from zid_platform.migrations import execute_script as _run

revision = "verification_0023"
down_revision = "verification_0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _run("""
        CREATE TABLE verify.check_contact (
            contact_id      uuid PRIMARY KEY,
            tenant_id       uuid NOT NULL,
            case_id         uuid NOT NULL REFERENCES verify.check_case (case_id),
            kind            text NOT NULL CHECK (kind IN ('EMAIL', 'PHONE')),
            revision        integer NOT NULL CHECK (revision >= 1),
            value_hash      text NOT NULL CHECK (value_hash ~ '^[0-9a-f]{64}$'),
            vault_object_id text NOT NULL,
            masked          text NOT NULL CHECK (length(masked) BETWEEN 3 AND 300),
            region          text CHECK (region IS NULL OR region ~ '^[A-Z0-9]{2,3}$'),
            calling_code    text CHECK (calling_code IS NULL OR calling_code ~ '^[1-9][0-9]{0,2}$'),
            unusual         boolean NOT NULL DEFAULT false,
            created_at      timestamptz NOT NULL,
            superseded_at   timestamptz,
            UNIQUE (case_id, kind, revision)
        );
        CREATE UNIQUE INDEX check_contact_current ON verify.check_contact (case_id, kind) WHERE superseded_at IS NULL;

        CREATE FUNCTION verify.check_contact_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'contacts are kept; erasure is a privacy workflow' USING ERRCODE = 'check_violation';
            END IF;
            IF TG_OP = 'UPDATE' AND (
                (NEW.contact_id, NEW.tenant_id, NEW.case_id, NEW.kind, NEW.revision, NEW.value_hash, NEW.vault_object_id, NEW.masked, NEW.created_at)
                IS DISTINCT FROM
                (OLD.contact_id, OLD.tenant_id, OLD.case_id, OLD.kind, OLD.revision, OLD.value_hash, OLD.vault_object_id, OLD.masked, OLD.created_at)
                OR OLD.superseded_at IS NOT NULL
            ) THEN
                RAISE EXCEPTION 'a contact is only ever superseded, once' USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER check_contact_guard BEFORE UPDATE OR DELETE ON verify.check_contact
            FOR EACH ROW EXECUTE FUNCTION verify.check_contact_guard();

        CREATE TABLE verify.check_challenge (
            challenge_id     uuid PRIMARY KEY,
            tenant_id        uuid NOT NULL,
            contact_id       uuid NOT NULL REFERENCES verify.check_contact (contact_id),
            contact_revision integer NOT NULL,
            channel          text NOT NULL CHECK (channel IN ('EMAIL', 'SMS', 'VOICE')),
            session_ref      text NOT NULL CHECK (length(session_ref) BETWEEN 1 AND 128),
            secret_hash      bytea NOT NULL CHECK (octet_length(secret_hash) = 32),
            state            text NOT NULL CHECK (state IN ('PENDING', 'SENT', 'DELIVERED_UNKNOWN', 'VERIFIED', 'EXPIRED', 'REVOKED')),
            expires_at       timestamptz NOT NULL,
            attempt_count    integer NOT NULL CHECK (attempt_count BETWEEN 0 AND 5),
            send_count       integer NOT NULL CHECK (send_count BETWEEN 1 AND 3),
            last_sent_at     timestamptz NOT NULL,
            created_at       timestamptz NOT NULL,
            consumed_at      timestamptz,
            end_reason       text,
            CHECK (expires_at > created_at AND expires_at <= created_at + interval '5 minutes'),
            CHECK ((state = 'VERIFIED') = (consumed_at IS NOT NULL))
        );
        CREATE INDEX check_challenge_contact ON verify.check_challenge (tenant_id, contact_id, created_at DESC);

        CREATE FUNCTION verify.check_challenge_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'challenges are kept' USING ERRCODE = 'check_violation';
            END IF;
            IF OLD.state IN ('VERIFIED', 'EXPIRED', 'REVOKED') THEN
                RAISE EXCEPTION 'a final challenge never changes' USING ERRCODE = 'check_violation';
            END IF;
            IF (NEW.challenge_id, NEW.tenant_id, NEW.contact_id, NEW.contact_revision, NEW.channel, NEW.session_ref, NEW.expires_at, NEW.created_at)
               IS DISTINCT FROM
               (OLD.challenge_id, OLD.tenant_id, OLD.contact_id, OLD.contact_revision, OLD.channel, OLD.session_ref, OLD.expires_at, OLD.created_at)
               OR NEW.attempt_count < OLD.attempt_count OR NEW.send_count < OLD.send_count THEN
                RAISE EXCEPTION 'a challenge keeps its binding and expiry, and its counters only go up' USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER check_challenge_guard BEFORE UPDATE OR DELETE ON verify.check_challenge
            FOR EACH ROW EXECUTE FUNCTION verify.check_challenge_guard();

        ALTER TABLE verify.check_contact ENABLE ROW LEVEL SECURITY;
        ALTER TABLE verify.check_contact FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant_isolation ON verify.check_contact
            USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
            WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);
        ALTER TABLE verify.check_challenge ENABLE ROW LEVEL SECURITY;
        ALTER TABLE verify.check_challenge FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant_isolation ON verify.check_challenge
            USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
            WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);
    """)


def downgrade() -> None:
    _run("""
        DROP TABLE verify.check_challenge;
        DROP FUNCTION verify.check_challenge_guard();
        DROP TABLE verify.check_contact;
        DROP FUNCTION verify.check_contact_guard();
    """)
