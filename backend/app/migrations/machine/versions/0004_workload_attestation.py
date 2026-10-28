"""Verified runtime attestations (ZID-MACHINE-001 §9, §21; CR-042).

Revision ID: machine_0004
Revises: machine_0003
Create Date: 2026-10-28

A workload token used to carry an attestation level the caller stated for itself. An attestation is evidence about
the runtime, so it must come from a party the platform trusts: an attestor signs a short statement (workload,
environment, artifact digest, level, lifetime) and the platform records the verified result here. Token issuance reads
this table and never the caller's claim. One row per verified or failed statement; rows are never edited, and a statement
that has passed its `valid_until` is simply not counted. Tenant-scoped under RLS.
"""

from zid_platform.migrations import execute_script as _run

revision = "machine_0004"
down_revision = "machine_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _run("""
        CREATE TABLE agent.workload_attestation (
            attestation_id       uuid PRIMARY KEY,
            tenant_id            uuid NOT NULL,
            workload_identity_id text NOT NULL,
            environment_id       text NOT NULL,
            artifact_digest      text NOT NULL,
            level                smallint NOT NULL CHECK (level BETWEEN 1 AND 3),
            verifier             text NOT NULL,
            claims_commitment    text NOT NULL CHECK (length(claims_commitment) = 64),
            status               text NOT NULL CHECK (status IN ('VALID', 'FAILED')),
            failure_code         text,
            verified_at          timestamptz NOT NULL DEFAULT now(),
            valid_until          timestamptz NOT NULL,
            CHECK ((status = 'FAILED') = (failure_code IS NOT NULL))
        );
        CREATE INDEX workload_attestation_lookup ON agent.workload_attestation
            (workload_identity_id, environment_id, artifact_digest, valid_until) WHERE status = 'VALID';
        ALTER TABLE agent.workload_attestation ENABLE ROW LEVEL SECURITY;
        ALTER TABLE agent.workload_attestation FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant_isolation ON agent.workload_attestation
            USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
            WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);
    """)


def downgrade() -> None:
    _run("DROP TABLE agent.workload_attestation;")
