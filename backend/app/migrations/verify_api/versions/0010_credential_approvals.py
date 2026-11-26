"""Two-person approval for a production credential (ZID-EXT-IDV-001 WF-05, section 05; CR-162).

Revision ID: verify_api_0010
Revises: verify_api_0009
Create Date: 2026-11-25

A production credential (a new one, or a rotation) is made only with an APPROVAL: one company administrator asks, a DIFFERENT one approves, and the approval is used once within a day. An approval names the application,
the action, the credential being rotated, and (for a private-key credential) a fingerprint of the exact public key, so it cannot be spent on anything else. Sandbox applications are not affected.
"""

from zid_platform.migrations import execute_script as _run

revision = "verify_api_0010"
down_revision = "verify_api_0009"
branch_labels = None
depends_on = None

TENANT = "tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid"


def upgrade() -> None:
    _run(f"""
        CREATE TABLE verify.api_credential_approval (
            approval_id     uuid PRIMARY KEY,
            tenant_id       uuid NOT NULL,
            application_id  uuid NOT NULL REFERENCES verify.api_application (application_id),
            action          text NOT NULL CHECK (action IN ('ISSUE', 'ROTATE')),
            credential_id   uuid REFERENCES verify.api_credential (credential_id),
            key_digest      bytea CHECK (key_digest IS NULL OR octet_length(key_digest) = 32),
            status          text NOT NULL DEFAULT 'PENDING' CHECK (status IN ('PENDING', 'APPROVED', 'CONSUMED', 'CANCELED', 'REJECTED')),
            requested_by    uuid NOT NULL,
            decided_by      uuid,
            note            text,
            created_at      timestamptz NOT NULL DEFAULT now(),
            expires_at      timestamptz NOT NULL,
            decided_at      timestamptz,
            consumed_at     timestamptz,
            CHECK ((action = 'ROTATE') = (credential_id IS NOT NULL)),
            CHECK (decided_by IS NULL OR decided_by <> requested_by),
            CHECK ((status IN ('APPROVED', 'CONSUMED', 'REJECTED')) = (decided_by IS NOT NULL) OR status = 'CANCELED'),
            CHECK ((status = 'CONSUMED') = (consumed_at IS NOT NULL))
        );
        CREATE INDEX api_credential_approval_open ON verify.api_credential_approval (tenant_id, created_at DESC);
        ALTER TABLE verify.api_credential_approval ENABLE ROW LEVEL SECURITY;
        ALTER TABLE verify.api_credential_approval FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant_isolation ON verify.api_credential_approval USING ({TENANT}) WITH CHECK ({TENANT});
    """)


def downgrade() -> None:
    _run("""
        DROP TABLE verify.api_credential_approval;
    """)
