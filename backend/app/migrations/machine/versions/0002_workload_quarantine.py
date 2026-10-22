"""Workload environment quarantine (DEC-F7 §4; CRYPTO-MI-001 §29; MACH-10/21; CR-014).

Revision ID: machine_0002
Revises: machine_0001
Create Date: 2026-10-21

Urgent compromise is enforced through `security_revision` and quarantine, not CRL/OCSP: a
quarantined environment (or a SUSPENDED/REVOKED binding) refuses every credential and every
workload-to-principal resolution from the next request on. One row per quarantine episode;
release keeps the row (lifted_at) so the history stays auditable.
"""

from zid_platform.migrations import execute_script as _run

revision = "machine_0002"
down_revision = "machine_0001"
branch_labels = None
depends_on = None

TABLES = ("environment_quarantine",)


def upgrade() -> None:
    _run("""
        CREATE TABLE agent.environment_quarantine (
            quarantine_id            uuid PRIMARY KEY,
            tenant_id                uuid NOT NULL,
            environment_id           text NOT NULL,
            reason_code              text NOT NULL,
            security_revision        bigint NOT NULL CHECK (security_revision >= 1),
            quarantined_by           uuid,
            quarantined_at           timestamptz NOT NULL DEFAULT now(),
            lifted_at                timestamptz,
            lifted_by                uuid
        );
        CREATE UNIQUE INDEX environment_quarantine_active ON agent.environment_quarantine
            (tenant_id, environment_id) WHERE lifted_at IS NULL;
    """)
    for table in TABLES:
        _run(f"""
            ALTER TABLE agent.{table} ENABLE ROW LEVEL SECURITY;
            ALTER TABLE agent.{table} FORCE ROW LEVEL SECURITY;
            CREATE POLICY tenant_isolation ON agent.{table}
                USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
                WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);
        """)


def downgrade() -> None:
    _run("DROP TABLE agent.environment_quarantine;")
