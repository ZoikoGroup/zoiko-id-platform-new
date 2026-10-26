"""Human attestations for the agent activation gates (ZID-MACHINE-001 §12.1; CR-021).

Revision ID: machine_0003
Revises: machine_0002
Create Date: 2026-10-24

Four of the ten §12.1 activation gates are facts only a person can establish: the security review
passed, the suspend/revoke kill path was tested, credentials are short-lived, and the agent's actions
are observable. Each is recorded here by name with who attested it, when, and a short note, so
activation reads recorded attestations instead of a caller's claim. One row per (agent, gate); a gate
is re-attested by replacing its row. Tenant-scoped under RLS.
"""

from zid_platform.migrations import execute_script as _run

revision = "machine_0003"
down_revision = "machine_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _run("""
        CREATE TABLE agent.agent_gate_attestation (
            agent_principal_id  uuid NOT NULL REFERENCES agent.agent_profile (agent_principal_id),
            tenant_id           uuid NOT NULL,
            gate                text NOT NULL CHECK (gate IN
                                ('SECURITY_REVIEW', 'KILL_PATH_TESTED', 'SHORT_LIVED_CREDENTIALS', 'OBSERVABILITY')),
            attested_by         uuid NOT NULL,
            note                text NOT NULL DEFAULT '' CHECK (length(note) <= 1000),
            attested_at         timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (agent_principal_id, gate)
        );
        ALTER TABLE agent.agent_gate_attestation ENABLE ROW LEVEL SECURITY;
        ALTER TABLE agent.agent_gate_attestation FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant_isolation ON agent.agent_gate_attestation
            USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
            WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);
    """)


def downgrade() -> None:
    _run("DROP TABLE agent.agent_gate_attestation;")
