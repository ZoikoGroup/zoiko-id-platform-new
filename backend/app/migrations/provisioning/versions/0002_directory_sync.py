"""Directory sync: pull a company's users from its identity provider into one provisioning source.

Revision ID: provisioning_0002
Revises: provisioning_0001
Create Date: 2026-11-05

ZID-OVERLAY-001 §10, §14-16 (connector framework). A directory sync reads the people of the company's own directory (Auth0 first) with a
read-only credential and applies them to the SCIM source it is bound to, so the company's directory stays the source of truth for who works there.
- platform.directory_sync: one sync per provisioning source. `settings` holds the provider's addresses and ids only (never a secret); the secret
  is a write-only sealed credential of the integration connector named by `connector_id`. `last_error` is a code, never provider text.
"""

from zid_platform.migrations import execute_script as _run

revision = "provisioning_0002"
down_revision = "provisioning_0001"
branch_labels = None
depends_on = None

TENANT_TABLES = ("directory_sync",)


def upgrade() -> None:
    _run("""
        CREATE TABLE platform.directory_sync (
            sync_id              uuid PRIMARY KEY,
            tenant_id            uuid NOT NULL,
            source_id            uuid NOT NULL REFERENCES platform.provisioning_source (source_id),
            connector_id         uuid NOT NULL,
            provider             text NOT NULL CHECK (provider IN ('AUTH0')),
            display_name         text NOT NULL CHECK (char_length(display_name) <= 128),
            settings             jsonb NOT NULL DEFAULT '{}',
            status               text NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE', 'PAUSED')),
            interval_seconds     integer NOT NULL DEFAULT 3600 CHECK (interval_seconds BETWEEN 300 AND 86400),
            next_run_at          timestamptz NOT NULL DEFAULT now(),
            last_run_at          timestamptz,
            last_outcome         text CHECK (last_outcome IN ('OK', 'FAILED')),
            last_error           text CHECK (last_error IS NULL OR char_length(last_error) <= 64),
            last_counts          jsonb NOT NULL DEFAULT '{}',
            consecutive_failures integer NOT NULL DEFAULT 0 CHECK (consecutive_failures >= 0),
            revision             integer NOT NULL DEFAULT 1 CHECK (revision >= 1),
            created_at           timestamptz NOT NULL DEFAULT now(),
            updated_at           timestamptz NOT NULL DEFAULT now(),
            UNIQUE (source_id)
        );
        CREATE INDEX directory_sync_due ON platform.directory_sync (next_run_at) WHERE status = 'ACTIVE';
    """)
    for table in TENANT_TABLES:
        _run(f"""
            ALTER TABLE platform.{table} ENABLE ROW LEVEL SECURITY;
            ALTER TABLE platform.{table} FORCE ROW LEVEL SECURITY;
            CREATE POLICY tenant_isolation ON platform.{table}
                USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
                WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);
        """)


def downgrade() -> None:
    _run("DROP TABLE platform.directory_sync;")
