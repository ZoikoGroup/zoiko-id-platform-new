"""Organization identifier links, legal-name history and source quarantine (ZID-ROOT-001 §4-6, §18, §22).

Revision ID: organization_0003
Revises: organization_0002
Create Date: 2026-10-25

organization_identifier: one canonical organization per tenant, reachable by every identifier it is
known by (national number, EUID, LEI). An identifier belongs to at most one organization, so a
second claim on the same identifier is a conflict, never a silent merge (ROOT-01, ROOT-02). A
national number is only meaningful with its jurisdiction; LEI and EUID are global (jurisdiction '').

organization_name: the legal-name history. A rename closes the current row and opens a new one; the
organization id, its roots and its grants are untouched (ROOT-03).

source_quarantine: a registry/credential source that may be compromised. While a source is
QUARANTINED no new root is established from it and its existing roots are suspended; its past
observations stay as evidence (ROOT-25).
"""

from zid_platform.migrations import execute_script as _run

revision = "organization_0003"
down_revision = "organization_0002"
branch_labels = None
depends_on = None

TABLES = ("organization_identifier", "organization_name", "source_quarantine")


def upgrade() -> None:
    _run("""
        CREATE TABLE org.organization_identifier (
            tenant_id        uuid NOT NULL,
            organization_id  uuid NOT NULL REFERENCES org.organization (organization_id),
            namespace        text NOT NULL CHECK (namespace IN ('GB-COH', 'LEI', 'EUID')),
            jurisdiction     text NOT NULL DEFAULT '',
            identifier       text NOT NULL CHECK (identifier ~ '^[A-Z0-9][A-Z0-9._-]{4,39}$'),
            source_id        text NOT NULL,
            linked_at        timestamptz NOT NULL,
            PRIMARY KEY (tenant_id, namespace, jurisdiction, identifier),
            CHECK ((namespace IN ('LEI', 'EUID')) = (jurisdiction = ''))
        );
        CREATE INDEX organization_identifier_org ON org.organization_identifier (organization_id);

        CREATE TABLE org.organization_name (
            name_id          uuid PRIMARY KEY,
            tenant_id        uuid NOT NULL,
            organization_id  uuid NOT NULL REFERENCES org.organization (organization_id),
            legal_name       text NOT NULL,
            source_id        text NOT NULL,
            source_revision  text NOT NULL DEFAULT '',
            valid_from       timestamptz NOT NULL,
            valid_to         timestamptz
        );
        CREATE UNIQUE INDEX organization_name_current ON org.organization_name (organization_id)
            WHERE valid_to IS NULL;

        CREATE TABLE org.source_quarantine (
            tenant_id        uuid NOT NULL,
            source_id        text NOT NULL,
            status           text NOT NULL CHECK (status IN ('QUARANTINED', 'RELEASED')),
            reason           text NOT NULL,
            quarantined_at   timestamptz NOT NULL,
            released_at      timestamptz,
            PRIMARY KEY (tenant_id, source_id)
        );

        CREATE FUNCTION org.append_only_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION '% is append-only', TG_TABLE_NAME USING ERRCODE = 'check_violation';
            END IF;
            IF TG_TABLE_NAME = 'organization_name' THEN
                -- history: only the open row's valid_to may be set, once
                IF OLD.valid_to IS NOT NULL OR NEW.valid_to IS NULL
                   OR (NEW.name_id, NEW.tenant_id, NEW.organization_id, NEW.legal_name, NEW.valid_from)
                      IS DISTINCT FROM (OLD.name_id, OLD.tenant_id, OLD.organization_id, OLD.legal_name, OLD.valid_from) THEN
                    RAISE EXCEPTION 'organization_name history is append-only' USING ERRCODE = 'check_violation';
                END IF;
            ELSE
                RAISE EXCEPTION '% is append-only', TG_TABLE_NAME USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER organization_name_history BEFORE UPDATE OR DELETE ON org.organization_name
            FOR EACH ROW EXECUTE FUNCTION org.append_only_guard();
        CREATE TRIGGER organization_identifier_append_only BEFORE UPDATE OR DELETE ON org.organization_identifier
            FOR EACH ROW EXECUTE FUNCTION org.append_only_guard();
    """)
    for table in TABLES:
        _run(f"""
            ALTER TABLE org.{table} ENABLE ROW LEVEL SECURITY;
            ALTER TABLE org.{table} FORCE ROW LEVEL SECURITY;
            CREATE POLICY tenant_isolation ON org.{table}
                USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
                WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);
        """)


def downgrade() -> None:
    _run("""
        DROP TABLE org.source_quarantine;
        DROP TABLE org.organization_name;
        DROP TABLE org.organization_identifier;
        DROP FUNCTION org.append_only_guard();
    """)
