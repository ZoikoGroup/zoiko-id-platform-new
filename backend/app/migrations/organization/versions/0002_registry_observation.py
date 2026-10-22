"""Registry source observations and root bindings (ZID-ROOT-001 §4, §6, §19; G-W1; CR-015).

Revision ID: organization_0002
Revises: organization_0001
Create Date: 2026-10-22

registry_observation: append-only provenance of every registry lookup and refresh - source,
namespace, the public registration identifier, the source-native status and revision, what it meant
(ESTABLISHED / FRESH / OFFICER_CEASED / DISSOLVED / NOT_ELIGIBLE), and for a registry root the root it
backs and the commitment of the registered officer the holder was bound to (never the name). Public
register identifiers are business data, not personal data, so they are kept to re-query the source.
"""

from zid_platform.migrations import execute_script as _run

revision = "organization_0002"
down_revision = "organization_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _run("""
        CREATE TABLE org.registry_observation (
            observation_id       uuid PRIMARY KEY,
            tenant_id            uuid NOT NULL,
            organization_id      uuid NOT NULL REFERENCES org.organization (organization_id),
            root_assertion_id    uuid,
            source_id            text NOT NULL,
            source_type          text NOT NULL CHECK (source_type IN ('NATIONAL_REGISTER', 'GLEIF')),
            namespace            text NOT NULL,
            identifier           text NOT NULL CHECK (identifier ~ '^[A-Z0-9]{6,20}$'),
            source_status        text NOT NULL,
            source_revision      text NOT NULL DEFAULT '',
            officer_key          text,
            outcome              text NOT NULL CHECK (outcome IN ('ESTABLISHED', 'CORROBORATED', 'FRESH',
                                 'OFFICER_CEASED', 'DISSOLVED', 'NOT_ELIGIBLE')),
            observed_at          timestamptz NOT NULL,
            CHECK (root_assertion_id IS NULL OR officer_key IS NOT NULL)
        );
        CREATE INDEX registry_observation_root ON org.registry_observation (root_assertion_id, observed_at)
            WHERE root_assertion_id IS NOT NULL;
        ALTER TABLE org.registry_observation ENABLE ROW LEVEL SECURITY;
        ALTER TABLE org.registry_observation FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant_isolation ON org.registry_observation
            USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
            WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);
        CREATE FUNCTION org.registry_observation_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'registry observations are append-only' USING ERRCODE = 'check_violation';
        END $$;
        CREATE TRIGGER registry_observation_append_only BEFORE UPDATE OR DELETE ON org.registry_observation
            FOR EACH ROW EXECUTE FUNCTION org.registry_observation_guard();
    """)


def downgrade() -> None:
    _run("""
        DROP TABLE org.registry_observation;
        DROP FUNCTION org.registry_observation_guard();
    """)
