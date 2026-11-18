"""Evidence packages for a screening case: asked for, approved, built, fetched (ZID-DTV-WF-06 S13, ENG-16, QA-043; CR-113).

Revision ID: screening_0010
Revises: screening_0009
Create Date: 2026-11-18

screening_evidence_export: one row per package. A package without sensitive material is READY as soon as it is built; one with sensitive material is PENDING until a second person, who is not the requester,
approves it. The bundle stored is already redacted (personal fields are hash tokens) and carries its manifest hash, so what is held is what an auditor is handed. A package expires and is then EXPIRED, and its
bundle is cleared. A screening reviewer can read the row across tenants (to find which tenant an export belongs to) only when the session says so.
"""

from zid_platform.migrations import execute_script as _run

revision = "screening_0010"
down_revision = "screening_0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _run("""
        CREATE TABLE verify.screening_evidence_export (
            export_id          uuid PRIMARY KEY,
            request_id         uuid NOT NULL REFERENCES verify.screening_request (request_id),
            tenant_id          uuid NOT NULL,
            purpose            text NOT NULL CHECK (length(purpose) BETWEEN 10 AND 500),
            requester          text NOT NULL,
            approver           text,
            include_sensitive  boolean NOT NULL,
            state              text NOT NULL CHECK (state IN ('PENDING', 'READY', 'EXPIRED')),
            bundle             jsonb,
            manifest_hash      text CHECK (manifest_hash IS NULL OR manifest_hash ~ '^[0-9a-f]{64}$'),
            ttl_seconds        integer NOT NULL CHECK (ttl_seconds BETWEEN 60 AND 2592000),
            created_at         timestamptz NOT NULL,
            expires_at         timestamp with time zone,
            CHECK (state <> 'READY' OR bundle IS NOT NULL),
            CHECK (NOT include_sensitive OR state = 'PENDING' OR approver IS NOT NULL)
        );
        CREATE INDEX screening_evidence_export_case ON verify.screening_evidence_export (request_id, created_at);
        ALTER TABLE verify.screening_evidence_export ENABLE ROW LEVEL SECURITY;
        ALTER TABLE verify.screening_evidence_export FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant_isolation ON verify.screening_evidence_export
            USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
            WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);
        CREATE POLICY reviewer_read ON verify.screening_evidence_export FOR SELECT
            USING (current_setting('app.screening_reviewer', true) = 'on');
    """)


def downgrade() -> None:
    _run("DROP TABLE verify.screening_evidence_export;")
