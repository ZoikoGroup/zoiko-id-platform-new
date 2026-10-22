"""Workspace directory: tenant slug -> its first-party console client (G-W0 sign-up; CR-015).

Revision ID: tenant_0004
Revises: tenant_0003
Create Date: 2026-10-22

The one lookup made before a tenant is known, like authn.client_directory: a person types their
company's workspace name to sign in, and the web app learns which console client (hence which
tenant) to sign them in through. It holds no personal data and grants nothing - the cell still maps
the client to its tenant and the person must authenticate there. Not tenant-scoped (no RLS).
"""

from zid_platform.migrations import execute_script as _run

revision = "tenant_0004"
down_revision = "tenant_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _run("""
        CREATE TABLE platform.workspace_directory (
            workspace          text PRIMARY KEY
                               CHECK (workspace ~ '^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$'),
            tenant_id          uuid NOT NULL UNIQUE,
            console_client_id  text NOT NULL UNIQUE,
            display_name       text NOT NULL CHECK (length(display_name) BETWEEN 1 AND 200),
            created_at         timestamptz NOT NULL DEFAULT now()
        );
    """)


def downgrade() -> None:
    _run("DROP TABLE platform.workspace_directory;")
