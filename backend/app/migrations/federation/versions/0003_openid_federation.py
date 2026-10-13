"""OpenID Federation 1.1 trust anchors; federation-resolved connections.

Revision ID: federation_0003
Revises: federation_0002
Create Date: 2026-10-13

ZID-AUTH-001 §12, §17 (AUTH-W8); ZID-VC-001 trust federation.
- authn.federation_trust_anchor: anchors configured per tenant with pinned keys and the trust
  domains fetches may reach (discovery is convenience, never trust bootstrap).
- authn.federation_connection: an OIDC connection may name a federation entity and an anchor
  instead of pinned endpoints/keys; those then come from a validated trust chain at login time.
"""

from zid_platform.migrations import execute_script as _run

revision = "federation_0003"
down_revision = "federation_0002"
branch_labels = None
depends_on = None

TENANT_TABLES = ("federation_trust_anchor",)


def upgrade() -> None:
    _run("""
        CREATE TABLE authn.federation_trust_anchor (
            anchor_id         uuid PRIMARY KEY,
            tenant_id         uuid NOT NULL,
            entity_id         text NOT NULL CHECK (entity_id LIKE 'https://%'),
            jwks              jsonb NOT NULL CHECK (jsonb_typeof(jwks -> 'keys') = 'array'),
            allowed_domains   text[] NOT NULL CHECK (cardinality(allowed_domains) >= 1),
            status            text NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE', 'QUARANTINED', 'RETIRED')),
            revision          integer NOT NULL DEFAULT 1,
            created_at        timestamptz NOT NULL DEFAULT now(),
            UNIQUE (tenant_id, entity_id)
        );

        ALTER TABLE authn.federation_connection
            ADD COLUMN trust_anchor_id uuid REFERENCES authn.federation_trust_anchor (anchor_id),
            ADD COLUMN federation_entity_id text CHECK (federation_entity_id IS NULL OR federation_entity_id LIKE 'https://%'),
            ADD CONSTRAINT federation_connection_resolved CHECK (
                (trust_anchor_id IS NULL) = (federation_entity_id IS NULL));
        ALTER TABLE authn.federation_connection DROP CONSTRAINT federation_connection_check;
        ALTER TABLE authn.federation_connection ADD CONSTRAINT federation_connection_oidc CHECK (
            protocol <> 'OIDC' OR (client_id IS NOT NULL AND client_auth IS NOT NULL AND (
                federation_entity_id IS NOT NULL OR (
                    authorization_endpoint LIKE 'https://%' AND token_endpoint LIKE 'https://%'
                    AND jsonb_typeof(jwks -> 'keys') = 'array'))));
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
        ALTER TABLE authn.federation_connection DROP CONSTRAINT federation_connection_oidc;
        ALTER TABLE authn.federation_connection ADD CONSTRAINT federation_connection_check CHECK (
            protocol <> 'OIDC' OR (client_id IS NOT NULL AND client_auth IS NOT NULL
                AND authorization_endpoint LIKE 'https://%' AND token_endpoint LIKE 'https://%'
                AND jsonb_typeof(jwks -> 'keys') = 'array'));
        ALTER TABLE authn.federation_connection DROP CONSTRAINT federation_connection_resolved,
            DROP COLUMN federation_entity_id, DROP COLUMN trust_anchor_id;
        DROP TABLE authn.federation_trust_anchor;
    """)
