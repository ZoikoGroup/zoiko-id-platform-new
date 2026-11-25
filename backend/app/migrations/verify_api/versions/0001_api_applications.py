"""Partner Verify API: applications, credentials and access tokens (ZID-EXT-IDV-001 WF-05, SEC-01 to SEC-04; CR-149).

Revision ID: verify_api_0001
Revises:
Create Date: 2026-11-24

`api_application` is what a company registers to call the Verify API from its server: one environment, the scopes it holds, and the exact origins its hosted flow may return to.
`api_credential` is a client id and the HASH of a secret (the secret itself is shown once and never stored). `api_access_token` is the hash of a short-lived bearer token, with the
application, environment and scopes it was issued for, so a token can never be used for another application or environment.

All three are tenant-scoped under row-level security. A server calling the API has no signed-in person, so the token endpoint and the bearer check read these rows inside a transaction that has
put itself into `auth` scope (a second policy that opens only then), exactly as the commercial operator plane does for its own cross-company reads.
"""

from zid_platform.migrations import execute_script as _run

revision = "verify_api_0001"
down_revision = None
branch_labels = None
depends_on = None

TENANT = "tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid"
AUTH = "current_setting('app.verify_api_scope', true) = 'auth'"
TABLES = ("api_application", "api_credential", "api_access_token")


def upgrade() -> None:
    policies = "\n".join(
        f"""
        ALTER TABLE verify.{t} ENABLE ROW LEVEL SECURITY;
        ALTER TABLE verify.{t} FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant_isolation ON verify.{t} USING ({TENANT}) WITH CHECK ({TENANT});
        CREATE POLICY verify_api_auth ON verify.{t} USING ({AUTH}) WITH CHECK ({AUTH});
        """
        for t in TABLES
    )
    _run(f"""
        CREATE TABLE verify.api_application (
            application_id    uuid PRIMARY KEY,
            tenant_id         uuid NOT NULL,
            name              text NOT NULL CHECK (char_length(name) BETWEEN 3 AND 80),
            environment       text NOT NULL CHECK (environment IN ('SANDBOX', 'PRODUCTION')),
            status            text NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE', 'SUSPENDED', 'RETIRED')),
            scopes            text[] NOT NULL CHECK (cardinality(scopes) > 0),
            redirect_origins  text[] NOT NULL DEFAULT '{{}}',
            created_by        uuid NOT NULL,
            created_at        timestamptz NOT NULL DEFAULT now(),
            updated_at        timestamptz NOT NULL DEFAULT now()
        );
        -- one live application of a name per environment for a company; a retired one frees the name
        CREATE UNIQUE INDEX api_application_name ON verify.api_application (tenant_id, lower(name), environment)
            WHERE status <> 'RETIRED';

        CREATE TABLE verify.api_credential (
            credential_id     uuid PRIMARY KEY,
            application_id    uuid NOT NULL REFERENCES verify.api_application (application_id),
            tenant_id         uuid NOT NULL,
            client_id         text NOT NULL UNIQUE CHECK (client_id ~ '^zpc_[0-9a-f]{{20}}$'),
            secret_hash       bytea NOT NULL CHECK (octet_length(secret_hash) = 32),
            secret_hint       text NOT NULL CHECK (char_length(secret_hint) = 4),
            status            text NOT NULL CHECK (status IN ('ACTIVE', 'ROTATING', 'REVOKED')),
            created_by        uuid NOT NULL,
            created_at        timestamptz NOT NULL DEFAULT now(),
            expires_at        timestamptz NOT NULL,
            last_used_at      timestamptz,
            revoked_at        timestamptz,
            replaced_by       uuid,
            CHECK ((status = 'REVOKED') = (revoked_at IS NOT NULL))
        );
        CREATE INDEX api_credential_application ON verify.api_credential (application_id);

        CREATE TABLE verify.api_access_token (
            token_hash        bytea PRIMARY KEY CHECK (octet_length(token_hash) = 32),
            credential_id     uuid NOT NULL REFERENCES verify.api_credential (credential_id),
            application_id    uuid NOT NULL REFERENCES verify.api_application (application_id),
            tenant_id         uuid NOT NULL,
            environment       text NOT NULL CHECK (environment IN ('SANDBOX', 'PRODUCTION')),
            scopes            text[] NOT NULL CHECK (cardinality(scopes) > 0),
            issued_at         timestamptz NOT NULL DEFAULT now(),
            expires_at        timestamptz NOT NULL
        );
        CREATE INDEX api_access_token_expiry ON verify.api_access_token (expires_at);
        {policies}
    """)


def downgrade() -> None:
    _run("""
        DROP TABLE verify.api_access_token;
        DROP TABLE verify.api_credential;
        DROP TABLE verify.api_application;
    """)
