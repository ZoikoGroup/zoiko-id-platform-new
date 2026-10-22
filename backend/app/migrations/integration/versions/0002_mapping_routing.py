"""Mapping releases and multi-IdP routing rules (ZID-OVERLAY-001 §8, §11, §20; G-W3; CR-015).

Revision ID: integration_0002
Revises: integration_0001
Create Date: 2026-10-22

- mapping_release: immutable typed rules per connector (`scope_connector_id`) and version; DRAFT ->
  REVIEW -> ACTIVE -> RETIRED. Rules, their content hash and author never change after insert
  (trigger); one ACTIVE release per connector; activation needs a simulation report without
  conflicts and an approver other than the author (CHECK).
- identity_routing_rule: APPLICATION or DOMAIN -> federation connection; (kind, match) unique per
  tenant among ACTIVE rules, so routing is deterministic; rules are retired, never deleted.
"""

from zid_platform.migrations import execute_script as _run

revision = "integration_0002"
down_revision = "integration_0001"
branch_labels = None
depends_on = None

TABLES = ("mapping_release", "identity_routing_rule")


def upgrade() -> None:
    _run("""
        CREATE TABLE platform.mapping_release (
            release_id            uuid PRIMARY KEY,
            tenant_id             uuid NOT NULL,
            scope_connector_id    uuid NOT NULL REFERENCES platform.integration_connector (connector_id),
            version               integer NOT NULL CHECK (version >= 1),
            status                text NOT NULL DEFAULT 'DRAFT'
                                  CHECK (status IN ('DRAFT', 'REVIEW', 'ACTIVE', 'RETIRED')),
            rules                 jsonb NOT NULL,
            content_hash          text NOT NULL,
            author_principal_id   uuid NOT NULL,
            approved_by           uuid,
            simulation_report     jsonb,
            simulated_at          timestamptz,
            created_at            timestamptz NOT NULL DEFAULT now(),
            activated_at          timestamptz,
            retired_at            timestamptz,
            UNIQUE (scope_connector_id, version),
            CHECK (status NOT IN ('ACTIVE') OR (approved_by IS NOT NULL AND approved_by <> author_principal_id
                   AND simulation_report IS NOT NULL))
        );
        CREATE UNIQUE INDEX mapping_release_one_active ON platform.mapping_release (scope_connector_id)
            WHERE status = 'ACTIVE';

        CREATE FUNCTION platform.mapping_release_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.rules IS DISTINCT FROM OLD.rules OR NEW.content_hash <> OLD.content_hash
               OR NEW.author_principal_id <> OLD.author_principal_id OR NEW.version <> OLD.version
               OR NEW.scope_connector_id <> OLD.scope_connector_id THEN
                RAISE EXCEPTION 'mapping releases are immutable; create a new version'
                    USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER mapping_release_guard BEFORE UPDATE ON platform.mapping_release
            FOR EACH ROW EXECUTE FUNCTION platform.mapping_release_guard();

        CREATE TABLE platform.identity_routing_rule (
            rule_id          uuid PRIMARY KEY,
            tenant_id        uuid NOT NULL,
            kind             text NOT NULL CHECK (kind IN ('APPLICATION', 'DOMAIN')),
            match            text NOT NULL,
            connection_ref   text NOT NULL CHECK (connection_ref LIKE 'federation_connection:%'),
            status           text NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE', 'RETIRED')),
            created_by       uuid NOT NULL,
            created_at       timestamptz NOT NULL DEFAULT now()
        );
        CREATE UNIQUE INDEX identity_route_unique ON platform.identity_routing_rule (tenant_id, kind, match)
            WHERE status = 'ACTIVE';
    """)
    for table in TABLES:
        _run(f"""
            ALTER TABLE platform.{table} ENABLE ROW LEVEL SECURITY;
            ALTER TABLE platform.{table} FORCE ROW LEVEL SECURITY;
            CREATE POLICY tenant_isolation ON platform.{table}
                USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
                WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);
        """)


def downgrade() -> None:
    _run("""
        DROP TABLE platform.identity_routing_rule;
        DROP TABLE platform.mapping_release;
        DROP FUNCTION platform.mapping_release_guard();
    """)
