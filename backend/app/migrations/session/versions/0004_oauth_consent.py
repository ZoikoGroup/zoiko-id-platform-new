"""OAuth consent for third-party clients.

Revision ID: session_0004
Revises: session_0003
Create Date: 2026-10-13

ZID-AUTH-001 §6.2; ZID-PRIVACY-001 consent principles.
- authn.oauth_consent: what a principal allowed a third-party client (scopes), until revoked;
  at most one live consent per (principal, client); revocation is recorded, never deleted.
- authn.consent_request: a pending authorization waiting for the user's decision - bound to the
  identity session that started it and to the exact validated request; single use, <= 10 min.
"""

from zid_platform.migrations import execute_script as _run

revision = "session_0004"
down_revision = "session_0003"
branch_labels = None
depends_on = None

TENANT_TABLES = ("oauth_consent", "consent_request")


def upgrade() -> None:
    _run("""
        CREATE TABLE authn.oauth_consent (
            consent_id     uuid PRIMARY KEY,
            tenant_id      uuid NOT NULL,
            principal_id   uuid NOT NULL,
            client_id      text NOT NULL,
            scopes         text[] NOT NULL,
            granted_at     timestamptz NOT NULL,
            revoked_at     timestamptz
        );
        CREATE UNIQUE INDEX oauth_consent_live ON authn.oauth_consent (tenant_id, principal_id, client_id)
            WHERE revoked_at IS NULL;

        CREATE TABLE authn.consent_request (
            request_hash   bytea PRIMARY KEY CHECK (octet_length(request_hash) = 32),
            tenant_id      uuid NOT NULL,
            principal_id   uuid NOT NULL,
            session_id     uuid NOT NULL,
            client_id      text NOT NULL,
            scopes         text[] NOT NULL,
            params         jsonb NOT NULL,
            pushed         boolean NOT NULL DEFAULT false,
            expires_at     timestamptz NOT NULL,
            used_at        timestamptz,
            created_at     timestamptz NOT NULL DEFAULT now(),
            CHECK (expires_at <= created_at + interval '10 minutes')
        );
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
        DROP TABLE authn.consent_request;
        DROP TABLE authn.oauth_consent;
    """)
