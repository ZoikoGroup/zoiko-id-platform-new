"""The PEP's OAuth client on an application registration (ZID-OVERLAY-001 §12-14; G-W4; CR-015).

Revision ID: integration_0003
Revises: integration_0002
Create Date: 2026-10-22

An AuthZEN caller is a PEP authenticated as an OAuth client; `pep_client_id` ties that client to
the application whose PEP profile (enforceable obligations, required context) governs its calls.
One application per PEP client per tenant.
"""

from zid_platform.migrations import execute_script as _run

revision = "integration_0003"
down_revision = "integration_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _run("""
        ALTER TABLE platform.integration_application ADD COLUMN pep_client_id text;
        CREATE UNIQUE INDEX integration_application_pep_client ON platform.integration_application
            (tenant_id, pep_client_id) WHERE pep_client_id IS NOT NULL AND status <> 'RETIRED';
    """)


def downgrade() -> None:
    _run("""
        DROP INDEX platform.integration_application_pep_client;
        ALTER TABLE platform.integration_application DROP COLUMN pep_client_id;
    """)
