"""A partner's logo on the verification page (ZID-EXT-IDV-001 WF-07, epic E19; CR-166; docs/specs/verify-api-mtls-domains-logos-decisions.md).

Revision ID: verify_api_0012
Revises: verify_api_0011
Create Date: 2026-11-25

One logo per application. It is UPLOADED (never linked, so the partner can never learn who opened the page or change the image after approval), scanned for malware, re-encoded by the imaging service to a plain image
with its metadata removed, and size-limited. A SANDBOX application's logo shows at once; a PRODUCTION application's logo is shown to customers only after an operator has approved that exact image, identified by its digest, so a
partner cannot put another brand's logo in front of real customers. Removing a logo empties it and keeps the row (the runtime role never deletes).
"""

from zid_platform.migrations import execute_script as _run

revision = "verify_api_0012"
down_revision = "verify_api_0011"
branch_labels = None
depends_on = None

TENANT = "tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid"
SCOPE = "current_setting('app.verify_api_scope', true)"


def upgrade() -> None:
    _run(f"""
        CREATE TABLE verify.api_logo (
            application_id  uuid PRIMARY KEY REFERENCES verify.api_application (application_id),
            tenant_id       uuid NOT NULL,
            status          text NOT NULL CHECK (status IN ('PENDING', 'APPROVED', 'REJECTED', 'REMOVED')),
            content_type    text NOT NULL DEFAULT 'image/jpeg' CHECK (content_type = 'image/jpeg'),
            image           bytea NOT NULL,
            digest          bytea NOT NULL CHECK (octet_length(digest) = 32),
            uploaded_by     uuid NOT NULL,
            uploaded_at     timestamptz NOT NULL DEFAULT now(),
            decided_by      uuid,
            decided_at      timestamptz,
            note            text,
            CHECK ((status = 'REMOVED' AND octet_length(image) = 0) OR (status <> 'REMOVED' AND octet_length(image) BETWEEN 100 AND 307200)),
            CHECK (status NOT IN ('REJECTED') OR decided_by IS NOT NULL)
        );
        CREATE INDEX api_logo_pending ON verify.api_logo (uploaded_at) WHERE status = 'PENDING';
        ALTER TABLE verify.api_logo ENABLE ROW LEVEL SECURITY;
        ALTER TABLE verify.api_logo FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant_isolation ON verify.api_logo USING ({TENANT}) WITH CHECK ({TENANT});
        CREATE POLICY verify_api_auth ON verify.api_logo FOR SELECT USING ({SCOPE} = 'auth');
        CREATE POLICY verify_api_operator_read ON verify.api_application FOR SELECT USING ({SCOPE} = 'operator');
        CREATE POLICY verify_api_operator ON verify.api_logo USING ({SCOPE} = 'operator') WITH CHECK ({SCOPE} = 'operator');
    """)


def downgrade() -> None:
    _run("""
        DROP POLICY verify_api_operator_read ON verify.api_application;
        DROP TABLE verify.api_logo;
    """)
