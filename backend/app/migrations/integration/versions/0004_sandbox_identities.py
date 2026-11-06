"""Developer sandbox test identities: labelled people a developer can decide for without a real person.

Revision ID: integration_0004
Revises: integration_0003
Create Date: 2026-11-05

Plan feature standard.developer-sandbox-test-identities. A test identity is an ordinary HUMAN principal of the company that is never invited, has
no passkey and no sign-in, so it can be named in a decision request (and given a grant) but never signs in or counts as an active user.
- platform.sandbox_identity: ties the principal to a label and an expiry; the worker disables it when it expires.
"""

from zid_platform.migrations import execute_script as _run

revision = "integration_0004"
down_revision = "integration_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _run("""
        CREATE TABLE platform.sandbox_identity (
            principal_id uuid PRIMARY KEY,
            tenant_id    uuid NOT NULL,
            label        text NOT NULL CHECK (char_length(label) BETWEEN 1 AND 64),
            created_by   uuid NOT NULL,
            created_at   timestamptz NOT NULL DEFAULT now(),
            expires_at   timestamptz NOT NULL,
            retired_at   timestamptz,
            UNIQUE (tenant_id, label)
        );
        CREATE INDEX sandbox_identity_due ON platform.sandbox_identity (expires_at) WHERE retired_at IS NULL;
        ALTER TABLE platform.sandbox_identity ENABLE ROW LEVEL SECURITY;
        ALTER TABLE platform.sandbox_identity FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant_isolation ON platform.sandbox_identity
            USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
            WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);
    """)


def downgrade() -> None:
    _run("DROP TABLE platform.sandbox_identity;")
