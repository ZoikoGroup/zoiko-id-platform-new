"""A changed security email takes effect only after the new address confirms it (ZID-DTV-WF-05 DTV-21, QA-014; Communication Estate ZID-EM-017 to
019; CR-114).

Revision ID: communication_0002
Revises: communication_0001
Create Date: 2026-11-18

email_contact_change: one pending change of a principal's own security email. The new address is encrypted in the evidence vault; here only the vault
object id, a keyed hash of the address and a keyed hash of the single-use link secret. A new request cancels the earlier pending one. A decided row
(APPLIED, EXPIRED, CANCELLED) never changes; rows are never deleted. Tenant data under row-level security.
"""

from zid_platform.migrations import execute_script as _run

revision = "communication_0002"
down_revision = "communication_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _run("""
        CREATE TABLE platform.email_contact_change (
            change_id       uuid PRIMARY KEY,
            tenant_id       uuid NOT NULL,
            principal_id    uuid NOT NULL,
            address_hash    text NOT NULL,
            vault_object_id text NOT NULL,
            token_hash      bytea NOT NULL CHECK (octet_length(token_hash) = 32),
            state           text NOT NULL CHECK (state IN ('PENDING', 'APPLIED', 'EXPIRED', 'CANCELLED')),
            attempt_count   integer NOT NULL DEFAULT 0 CHECK (attempt_count BETWEEN 0 AND 5),
            expires_at      timestamptz NOT NULL,
            created_at      timestamptz NOT NULL,
            decided_at      timestamptz,
            CHECK (expires_at > created_at AND expires_at <= created_at + interval '30 minutes'),
            CHECK ((state = 'PENDING') = (decided_at IS NULL))
        );
        CREATE UNIQUE INDEX email_contact_change_one_pending
            ON platform.email_contact_change (tenant_id, principal_id) WHERE state = 'PENDING';

        CREATE FUNCTION platform.email_contact_change_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'email changes are kept' USING ERRCODE = 'check_violation';
            END IF;
            IF OLD.state <> 'PENDING' THEN
                RAISE EXCEPTION 'a decided email change never changes' USING ERRCODE = 'check_violation';
            END IF;
            IF (NEW.change_id, NEW.tenant_id, NEW.principal_id, NEW.address_hash, NEW.vault_object_id, NEW.token_hash, NEW.expires_at, NEW.created_at)
               IS DISTINCT FROM
               (OLD.change_id, OLD.tenant_id, OLD.principal_id, OLD.address_hash, OLD.vault_object_id, OLD.token_hash, OLD.expires_at, OLD.created_at)
               OR NEW.attempt_count < OLD.attempt_count THEN
                RAISE EXCEPTION 'an email change keeps its binding; its attempts only go up' USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER email_contact_change_guard BEFORE UPDATE OR DELETE ON platform.email_contact_change
            FOR EACH ROW EXECUTE FUNCTION platform.email_contact_change_guard();

        ALTER TABLE platform.email_contact_change ENABLE ROW LEVEL SECURITY;
        ALTER TABLE platform.email_contact_change FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant_isolation ON platform.email_contact_change
            USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
            WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);
    """)


def downgrade() -> None:
    _run("""
        DROP TABLE platform.email_contact_change;
        DROP FUNCTION platform.email_contact_change_guard();
    """)
