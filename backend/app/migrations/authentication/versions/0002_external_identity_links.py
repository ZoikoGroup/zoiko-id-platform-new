"""External identity links (federation account linking).

Revision ID: authentication_0002
Revises: authentication_0001
Create Date: 2026-10-13

ZID-DATA-001 §5.2; ZID-AUTH-001 §9, §9.1 (AUTH-W4).
- issuer + subject is the stable key; email is never part of it.
- at most one ACTIVE link per (tenant, issuer, subject); a changed issuer/subject is a new link,
  the old one stays auditable as REVOKED; REVOKED is terminal; identity columns never change.
- `link_assurance` records how the link was established (explicit authenticated linking,
  enterprise provisioning proof, or just-in-time creation of a new principal).
"""

from zid_platform.migrations import execute_script as _run

revision = "authentication_0002"
down_revision = "authentication_0001"
branch_labels = None
depends_on = None

TENANT_TABLES = ("external_identity_link",)


def upgrade() -> None:
    _run("""
        CREATE TABLE authn.external_identity_link (
            link_id            uuid PRIMARY KEY,
            tenant_id          uuid NOT NULL,
            principal_id       uuid NOT NULL,
            issuer             text NOT NULL CHECK (char_length(issuer) BETWEEN 1 AND 512),
            subject            text NOT NULL CHECK (char_length(subject) BETWEEN 1 AND 255),
            connection_id      uuid,
            link_assurance     text NOT NULL CHECK (link_assurance IN
                                   ('EXPLICIT_AUTHENTICATED', 'ENTERPRISE_PROVISIONED', 'JIT')),
            status             text NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE', 'SUSPENDED', 'REVOKED')),
            created_at         timestamptz NOT NULL,
            last_asserted_at   timestamptz,
            revoked_at         timestamptz,
            revoked_reason     text
        );
        CREATE UNIQUE INDEX external_identity_link_active
            ON authn.external_identity_link (tenant_id, issuer, subject) WHERE status = 'ACTIVE';
        CREATE INDEX external_identity_link_principal ON authn.external_identity_link (tenant_id, principal_id);

        CREATE FUNCTION authn.external_identity_link_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF (NEW.link_id, NEW.tenant_id, NEW.principal_id, NEW.issuer, NEW.subject, NEW.link_assurance,
                NEW.created_at)
               IS DISTINCT FROM (OLD.link_id, OLD.tenant_id, OLD.principal_id, OLD.issuer, OLD.subject,
                                 OLD.link_assurance, OLD.created_at) THEN
                RAISE EXCEPTION 'a changed issuer/subject is a new link' USING ERRCODE = 'check_violation';
            END IF;
            IF OLD.status = 'REVOKED' AND NEW.status <> 'REVOKED' THEN
                RAISE EXCEPTION 'a revoked link stays revoked' USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER external_identity_link_guard BEFORE UPDATE ON authn.external_identity_link
            FOR EACH ROW EXECUTE FUNCTION authn.external_identity_link_guard();
    """)
    for table in TENANT_TABLES:
        _run(f"""
            ALTER TABLE authn.{table} ENABLE ROW LEVEL SECURITY;
            ALTER TABLE authn.{table} FORCE ROW LEVEL SECURITY;
            CREATE POLICY tenant_isolation ON authn.{table}
                USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
                WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);
        """)


def downgrade() -> None:
    _run("""
        DROP TRIGGER external_identity_link_guard ON authn.external_identity_link;
        DROP FUNCTION authn.external_identity_link_guard();
        DROP TABLE authn.external_identity_link;
    """)
