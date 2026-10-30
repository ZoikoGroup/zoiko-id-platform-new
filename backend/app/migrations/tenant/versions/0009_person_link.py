"""Which accounts are one natural person (ZID-ENG-DEC-007 invariant 2; slice L-7, CR-044).

Revision ID: tenant_0009
Revises: tenant_0008
Create Date: 2026-10-30

principal_person_link: one row per account that is known to belong to a person, naming the person it belongs to. Two accounts that
share a person_id are one human for separation of duties, however many accounts or delegations they use. Append-only: a link is
never rewritten by the runtime role, and a wrong link is corrected by a platform process, not by the tenant. Who may create a link
is an owner ruling (docs/signoff/roles-owner-pack.md); the table only keeps the fact.
"""

from zid_platform.migrations import execute_script as _run

revision = "tenant_0009"
down_revision = "tenant_0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _run("""
        CREATE TABLE platform.principal_person_link (
            tenant_id    uuid NOT NULL REFERENCES platform.tenant (tenant_id),
            principal_id uuid NOT NULL,
            person_id    uuid NOT NULL,
            source       text NOT NULL CHECK (source IN ('IDENTITY_PROOFING', 'PLATFORM_REVIEW', 'ENTERPRISE_DIRECTORY')),
            evidence_ref text CHECK (evidence_ref IS NULL OR length(evidence_ref) <= 128),
            linked_by    uuid,
            linked_at    timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (tenant_id, principal_id)
        );
        CREATE INDEX principal_person_link_person ON platform.principal_person_link (tenant_id, person_id);
        ALTER TABLE platform.principal_person_link ENABLE ROW LEVEL SECURITY;
        ALTER TABLE platform.principal_person_link FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant_isolation ON platform.principal_person_link
            USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
            WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);
        CREATE TRIGGER principal_person_link_append_only
            BEFORE UPDATE OR DELETE ON platform.principal_person_link
            FOR EACH ROW EXECUTE FUNCTION platform.tenant_history_guard();
    """)


def downgrade() -> None:
    _run("DROP TABLE platform.principal_person_link;")
