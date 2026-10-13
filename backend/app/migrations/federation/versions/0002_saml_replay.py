"""SAML assertion replay cache.

Revision ID: federation_0002
Revises: federation_0001
Create Date: 2026-10-13

ZID-AUTH-001 §10 "Replay: assertion ID replay cache through validity period" (AUTH-03, AUTH-10).
One row per accepted (issuer, assertion ID) until the assertion's NotOnOrAfter.
"""

from zid_platform.migrations import execute_script as _run

revision = "federation_0002"
down_revision = "federation_0001"
branch_labels = None
depends_on = None

TENANT_TABLES = ("saml_assertion_replay",)


def upgrade() -> None:
    _run("""
        CREATE TABLE authn.saml_assertion_replay (
            tenant_id      uuid NOT NULL,
            issuer         text NOT NULL,
            assertion_id   text NOT NULL CHECK (char_length(assertion_id) BETWEEN 1 AND 256),
            expires_at     timestamptz NOT NULL,
            PRIMARY KEY (tenant_id, issuer, assertion_id)
        );
        CREATE INDEX saml_assertion_replay_expiry ON authn.saml_assertion_replay (expires_at);
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
    _run("DROP TABLE authn.saml_assertion_replay;")
