"""Billing grants (ZID-COM-CL-002, ZID-UX-COM-010; slice L-14, CR-050).

Revision ID: tenant_0012
Revises: tenant_0011
Create Date: 2026-11-03

billing_grant: the Owner's gift of a bundle of billing permissions to one member. The permission list is frozen on the row, so a later
change to a bundle never widens an old grant. A row never changes after insert except to be revoked once (revoked_at and revoked_by);
a database trigger refuses every other edit and every delete, so the activity record of who could do what, and when, stays complete.
"""

from zid_platform.migrations import execute_script as _run

revision = "tenant_0012"
down_revision = "tenant_0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _run("""
        CREATE TABLE platform.billing_grant (
            grant_id               uuid PRIMARY KEY,
            tenant_id              uuid NOT NULL REFERENCES platform.tenant (tenant_id),
            grantee_principal_id   uuid NOT NULL,
            grantee_person_id      uuid NOT NULL,
            bundle                 text NOT NULL CHECK (bundle IN ('BILLING_ADMIN', 'BILLING_VIEWER', 'INVOICE_READER')),
            permissions            text[] NOT NULL CHECK (cardinality(permissions) > 0),
            reason                 text NOT NULL CHECK (length(reason) BETWEEN 1 AND 500),
            granted_by             uuid NOT NULL,
            expires_at             timestamptz,
            created_at             timestamptz NOT NULL DEFAULT now(),
            revoked_at             timestamptz,
            revoked_by             uuid,
            CHECK ((revoked_at IS NULL) = (revoked_by IS NULL)),
            CHECK (NOT ('billing.access.delegate' = ANY (permissions)))
        );
        CREATE INDEX billing_grant_grantee ON platform.billing_grant (tenant_id, grantee_principal_id);

        CREATE FUNCTION platform.billing_grant_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'billing grants are never deleted' USING ERRCODE = 'check_violation';
            END IF;
            IF (NEW.grant_id, NEW.tenant_id, NEW.grantee_principal_id, NEW.grantee_person_id, NEW.bundle, NEW.permissions,
                NEW.reason, NEW.granted_by, NEW.expires_at, NEW.created_at)
               IS DISTINCT FROM
               (OLD.grant_id, OLD.tenant_id, OLD.grantee_principal_id, OLD.grantee_person_id, OLD.bundle, OLD.permissions,
                OLD.reason, OLD.granted_by, OLD.expires_at, OLD.created_at) THEN
                RAISE EXCEPTION 'a billing grant is immutable; widening is a new grant' USING ERRCODE = 'check_violation';
            END IF;
            IF OLD.revoked_at IS NOT NULL THEN
                RAISE EXCEPTION 'a revoked billing grant is final' USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER billing_grant_guard BEFORE UPDATE OR DELETE ON platform.billing_grant
            FOR EACH ROW EXECUTE FUNCTION platform.billing_grant_guard();

        ALTER TABLE platform.billing_grant ENABLE ROW LEVEL SECURITY;
        ALTER TABLE platform.billing_grant FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant_isolation ON platform.billing_grant
            USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
            WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);
    """)


def downgrade() -> None:
    pass
