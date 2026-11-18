"""WF-05 postal confirmation codes (ZID-DTV-WF-05 A08, QA-028; CR-115).

Revision ID: verification_0025
Revises: verification_0024
Create Date: 2026-11-18

check_postal_challenge: a one-time code posted to one revision of a case's address. Only a keyed hash of the code is kept. The dispatch is recorded
(dispatch_ref, dispatched_at) when the mail house accepts it. A final state (VERIFIED, EXPIRED, REVOKED) never changes; the binding never changes;
attempts only go up; rows are never deleted. Tenant data under row-level security.
"""

from zid_platform.migrations import execute_script as _run

revision = "verification_0025"
down_revision = "verification_0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _run("""
        CREATE TABLE verify.check_postal_challenge (
            challenge_id     uuid PRIMARY KEY,
            tenant_id        uuid NOT NULL,
            case_id          uuid NOT NULL REFERENCES verify.check_case (case_id),
            address_id       uuid NOT NULL REFERENCES verify.check_address (address_id),
            address_revision integer NOT NULL CHECK (address_revision >= 1),
            secret_hash      bytea NOT NULL CHECK (octet_length(secret_hash) = 32),
            state            text NOT NULL CHECK (state IN ('PENDING_DISPATCH', 'DISPATCHED', 'VERIFIED', 'EXPIRED', 'REVOKED')),
            expires_at       timestamptz NOT NULL,
            attempt_count    integer NOT NULL CHECK (attempt_count BETWEEN 0 AND 5),
            created_at       timestamptz NOT NULL,
            dispatch_ref     text CHECK (dispatch_ref IS NULL OR length(dispatch_ref) BETWEEN 1 AND 128),
            dispatched_at    timestamptz,
            consumed_at      timestamptz,
            CHECK (expires_at >= created_at + interval '7 days' AND expires_at <= created_at + interval '60 days'),
            CHECK ((state = 'VERIFIED') = (consumed_at IS NOT NULL))
        );
        CREATE INDEX check_postal_challenge_case ON verify.check_postal_challenge (tenant_id, case_id, created_at DESC);

        CREATE FUNCTION verify.check_postal_challenge_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'postal codes are kept' USING ERRCODE = 'check_violation';
            END IF;
            IF OLD.state IN ('VERIFIED', 'EXPIRED', 'REVOKED') THEN
                RAISE EXCEPTION 'a final postal code never changes' USING ERRCODE = 'check_violation';
            END IF;
            IF (NEW.challenge_id, NEW.tenant_id, NEW.case_id, NEW.address_id, NEW.address_revision, NEW.secret_hash, NEW.expires_at, NEW.created_at)
               IS DISTINCT FROM
               (OLD.challenge_id, OLD.tenant_id, OLD.case_id, OLD.address_id, OLD.address_revision, OLD.secret_hash, OLD.expires_at, OLD.created_at)
               OR NEW.attempt_count < OLD.attempt_count THEN
                RAISE EXCEPTION 'a postal code keeps its binding and expiry; its attempts only go up' USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER check_postal_challenge_guard BEFORE UPDATE OR DELETE ON verify.check_postal_challenge
            FOR EACH ROW EXECUTE FUNCTION verify.check_postal_challenge_guard();

        ALTER TABLE verify.check_postal_challenge ENABLE ROW LEVEL SECURITY;
        ALTER TABLE verify.check_postal_challenge FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant_isolation ON verify.check_postal_challenge
            USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
            WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);
    """)


def downgrade() -> None:
    _run("""
        DROP TABLE verify.check_postal_challenge;
        DROP FUNCTION verify.check_postal_challenge_guard();
    """)
