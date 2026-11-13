"""The last property-check answer per property and host, kept so a CHANGE can be announced (ZID-INT-002 section 13; CR-071).

Revision ID: credential_0013
Revises: credential_0012
Create Date: 2026-11-12

`POST /v2/rooms/property-checks` was stateless, so nothing could say "the authority for this property changed". This keeps one row per
(property_ref, host_ref): the overall outcome and each dimension's outcome and reason codes. It holds the caller's opaque references and
outcomes only, never a document, a name, an address or any text from evidence.
"""

from zid_platform.migrations import execute_script as _run

revision = "credential_0013"
down_revision = "credential_0012"
branch_labels = None
depends_on = None

RULE = "(tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)"


def upgrade() -> None:
    _run(f"""
        CREATE TABLE cred.property_check_state (
            tenant_id      uuid NOT NULL,
            property_ref   text NOT NULL CHECK (char_length(property_ref) BETWEEN 1 AND 80),
            host_ref       text NOT NULL CHECK (char_length(host_ref) BETWEEN 1 AND 80),
            overall        text NOT NULL,
            dimensions     jsonb NOT NULL,
            checks_made    integer NOT NULL DEFAULT 1,
            updated_at     timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (tenant_id, property_ref, host_ref)
        );
        ALTER TABLE cred.property_check_state ENABLE ROW LEVEL SECURITY;
        ALTER TABLE cred.property_check_state FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant_isolation ON cred.property_check_state USING {RULE} WITH CHECK {RULE};
    """)


def downgrade() -> None:
    _run("DROP TABLE cred.property_check_state;")
